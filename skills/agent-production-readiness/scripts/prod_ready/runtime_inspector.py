"""Stage 2 (Post-Deployment): Read-Only GCP Live Runtime Inspector & Remediation Guide Generator.

Queries live Cloud Run services and Agent Runtime (Reasoning Engines) in a target
Google Cloud project in 100% READ-ONLY mode. Never mutates live resources; instead generates
tailored, copy-paste-ready remediation guides (`gcloud` CLI, `Terraform`, and Stage 1 `Agent Skill` prompts).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
from typing import Any


DEFAULT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "geap-sandbox-kwlee")
DEFAULT_REGION = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
SELF_SERVICE_NAME = os.environ.get("K_SERVICE", "prod-ready-workbench")
SNAPSHOT_PATH = Path(__file__).parent / "static" / "runtime_snapshot.json"


def _load_snapshot() -> dict[str, Any]:
    if SNAPSHOT_PATH.is_file():
        try:
            return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"cloud_run": {"items": []}, "agent_runtime": {"reasoningEngines": []}}


def _get_access_token() -> str | None:
    """Fetches a read-only OAuth2 access token from Cloud Run Metadata Server or local gcloud."""
    meta_url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    req = urllib.request.Request(meta_url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if token := data.get("access_token"):
                return str(token)
    except Exception:
        pass

    try:
        proc = subprocess.run(
            ["gcloud", "auth", "print-access-token", "--quiet"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def _gcp_get_json(url: str, token: str | None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_deployed_agents(
    project_id: str = DEFAULT_PROJECT_ID,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Lists live Cloud Run services and Agent Runtime instances in the given project/region (Read-Only)."""
    token = _get_access_token()
    agents: list[dict[str, Any]] = []
    errors: list[str] = []

    # 1. Query Cloud Run v1 API for regional services (with snapshot fallback if SA lacks roles/run.viewer)
    cr_url = f"https://{region}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{project_id}/services"
    try:
        data = _gcp_get_json(cr_url, token)
        cr_items = data.get("items", [])
    except Exception as exc:
        errors.append(f"Cloud Run API ({region}): {exc} (Using cached snapshot)")
        cr_items = _load_snapshot().get("cloud_run", {}).get("items", [])

    for item in cr_items:
        meta = item.get("metadata", {})
        name = meta.get("name", "")
        if not name or name == SELF_SERVICE_NAME:
            continue
        labels = meta.get("labels", {})
        annotations = meta.get("annotations", {})
        status_obj = item.get("status", {})
        url = status_obj.get("url", "")
        created_by = labels.get("created-by", annotations.get("run.googleapis.com/client-name", "gcloud"))
        is_adk = created_by in ("adk", "agents-cli") or "agent" in name.lower()
        agents.append(
            {
                "id": f"cloud_run:{region}:{name}",
                "name": name,
                "resource_id": name,
                "runtime_type": "cloud_run",
                "runtime_label": "Cloud Run",
                "project_id": project_id,
                "region": region,
                "url": url,
                "created_by": created_by,
                "is_agent": is_adk,
                "created_at": meta.get("creationTimestamp", ""),
            }
        )

    # 2. Query Agent Runtime (Reasoning Engines)
    ar_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{region}/reasoningEngines"
    try:
        ar_data = _gcp_get_json(ar_url, token)
        ar_items = ar_data.get("reasoningEngines", [])
    except Exception:
        ar_items = _load_snapshot().get("agent_runtime", {}).get("reasoningEngines", [])

    for eng in ar_items:
        full_name = eng.get("name", "")
        short_id = full_name.split("/")[-1] if full_name else ""
        disp = eng.get("displayName", "")
        spec = eng.get("spec", {})
        if not disp and not spec.get("agentFramework") and not spec.get("packageSpec"):
            continue
        agents.append(
            {
                "id": f"agent_runtime:{region}:{short_id}",
                "name": disp or f"ReasoningEngine ({short_id})",
                "resource_id": short_id,
                "runtime_type": "agent_runtime",
                "runtime_label": "Agent Runtime",
                "project_id": project_id,
                "region": region,
                "url": full_name,
                "created_by": spec.get("agentFramework", "vertex-ai"),
                "is_agent": True,
                "created_at": eng.get("createTime", ""),
            }
        )

    # Sort: Cloud Run ADK agents first, then Agent Runtime engines, then other Cloud Run services
    agents.sort(
        key=lambda x: (
            0 if (x["runtime_type"] == "cloud_run" and x["is_agent"]) else (1 if x["runtime_type"] == "agent_runtime" else 2),
            x["name"],
        )
    )
    return {
        "project_id": project_id,
        "region": region,
        "agent_count": len(agents),
        "agents": agents,
        "errors": errors,
    }


def _parse_memory_mib(mem_str: str) -> int:
    s = (mem_str or "").strip()
    if s.endswith("Gi"):
        return int(float(s[:-2]) * 1024)
    if s.endswith("G"):
        return int(float(s[:-1]) * 1000)
    if s.endswith("Mi"):
        return int(float(s[:-2]))
    if s.endswith("M"):
        return int(float(s[:-1]))
    return 512


def _parse_cpu_cores(cpu_str: str) -> float:
    s = (cpu_str or "").strip()
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    try:
        return float(s)
    except ValueError:
        return 1.0


def inspect_deployed_agent(
    project_id: str,
    region: str,
    service_name: str,
    runtime_type: str = "cloud_run",
) -> dict[str, Any]:
    """Performs a 100% Read-Only Production-Readiness inspection of a live Cloud Run / Agent Runtime service
    and returns step-by-step user remediation guides (gcloud CLI, Terraform, and Stage 1 Agent Skill prompts).
    """
    token = _get_access_token()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if runtime_type == "cloud_run":
        cr_url = f"https://{region}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{project_id}/services/{service_name}"
        try:
            svc = _gcp_get_json(cr_url, token)
        except Exception:
            snap_items = _load_snapshot().get("cloud_run", {}).get("items", [])
            svc = next((i for i in snap_items if i.get("metadata", {}).get("name") == service_name), {})
        return _evaluate_cloud_run_service(project_id, region, service_name, svc, now_str)
    else:
        ar_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{region}/reasoningEngines/{service_name}"
        try:
            eng = _gcp_get_json(ar_url, token)
        except Exception:
            snap_engs = _load_snapshot().get("agent_runtime", {}).get("reasoningEngines", [])
            eng = next((e for e in snap_engs if e.get("name", "").endswith(f"/{service_name}")), {})
        return _evaluate_agent_runtime_engine(project_id, region, service_name, eng, now_str)


def _extract_cloud_run_meta(svc: dict[str, Any], project_hint: str, region_hint: str) -> tuple[str, str, str]:
    meta = svc.get("metadata", {})
    labels = meta.get("labels", {})
    svc_name = meta.get("name") or svc.get("name", "my-agent").split("/")[-1]
    region = labels.get("cloud.googleapis.com/location") or region_hint or "us-central1"
    project_id = project_hint or "my-gcp-project"
    containers = svc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])
    if containers:
        for e in containers[0].get("env", []):
            if e.get("name") == "GOOGLE_CLOUD_PROJECT" and e.get("value"):
                project_id = str(e.get("value"))
                break
    return project_id, region, svc_name


def _extract_reasoning_engine_meta(eng: dict[str, Any], project_hint: str, region_hint: str) -> tuple[str, str, str]:
    full_name = eng.get("name", "")
    parts = full_name.split("/")
    project_id = project_hint or "my-gcp-project"
    region = region_hint or "us-central1"
    engine_id = full_name
    if len(parts) >= 6 and parts[0] == "projects" and parts[2] == "locations":
        project_id = project_hint if (project_hint and project_hint != "my-gcp-project") else parts[1]
        region = parts[3]
        engine_id = parts[5]
    return project_id, region, engine_id


def _sanitize_terminal_json(raw: str) -> str:
    """Cleans terminal-copied JSON where long lines (e.g., sha256 image URIs or URLs)
    were hard-wrapped with literal newlines inside JSON string literals, or where
    terminal prompts precede/follow the JSON payload.
    """
    text = (raw or "").strip()
    # Strip any leading/trailing terminal command prompt lines outside the JSON root
    first_brace = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    last_brace = max(text.rfind("}"), text.rfind("]"))
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace : last_brace + 1]

    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch in ("\r", "\n"):
                # Terminal line-wrap inside a JSON string literal: skip newline and wrapped indentation spaces
                i += 1
                while i < n and text[i] in (" ", "\t", "\r", "\n"):
                    i += 1
                continue
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
        i += 1
    return "".join(out)


def inspect_pasted_json(
    raw_json: str,
    project_id_hint: str = "",
    region_hint: str = "us-central1",
) -> dict[str, Any]:
    """Parses user-pasted CLI JSON output (Cloud Run service/list or Agent Runtime engine/list)
    without requiring any GCP IAM permissions on the server.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cleaned = _sanitize_terminal_json(raw_json)
    if not cleaned:
        raise ValueError("붙여넣은 JSON 내용이 비어 있습니다. 터미널 명령어 실행 결과를 붙여넣어 주세요.")

    parsed = json.loads(cleaned, strict=False)
    results: list[dict[str, Any]] = []

    # Case 1: Array of Cloud Run services (`gcloud run services list --format=json`)
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and ("spec" in item or "metadata" in item):
                p_id, reg, s_name = _extract_cloud_run_meta(item, project_id_hint, region_hint)
                results.append(_evaluate_cloud_run_service(p_id, reg, s_name, item, now_str))

    elif isinstance(parsed, dict):
        # Case 2: `{"reasoningEngines": [...]}` from Agent Runtime list API
        if "reasoningEngines" in parsed and isinstance(parsed["reasoningEngines"], list):
            for eng in parsed["reasoningEngines"]:
                disp = eng.get("displayName", "")
                spec = eng.get("spec", {})
                if not disp and not spec.get("agentFramework") and not spec.get("packageSpec"):
                    continue
                p_id, reg, e_id = _extract_reasoning_engine_meta(eng, project_id_hint, region_hint)
                results.append(_evaluate_agent_runtime_engine(p_id, reg, e_id, eng, now_str))

        # Case 3: `{"items": [...]}` from Cloud Run v1 list API
        elif "items" in parsed and isinstance(parsed["items"], list):
            for item in parsed["items"]:
                p_id, reg, s_name = _extract_cloud_run_meta(item, project_id_hint, region_hint)
                results.append(_evaluate_cloud_run_service(p_id, reg, s_name, item, now_str))

        # Case 4: Single Agent Runtime ReasoningEngine JSON
        elif "reasoningEngines/" in str(parsed.get("name", "")) or "deploymentSpec" in parsed.get("spec", {}) or "packageSpec" in parsed.get("spec", {}):
            p_id, reg, e_id = _extract_reasoning_engine_meta(parsed, project_id_hint, region_hint)
            results.append(_evaluate_agent_runtime_engine(p_id, reg, e_id, parsed, now_str))

        # Case 5: Single Cloud Run Service JSON (`gcloud run services describe <NAME> --format=json`)
        else:
            p_id, reg, s_name = _extract_cloud_run_meta(parsed, project_id_hint, region_hint)
            results.append(_evaluate_cloud_run_service(p_id, reg, s_name, parsed, now_str))

    if not results:
        raise ValueError("유효한 Cloud Run 서비스 또는 Agent Runtime(ReasoningEngine) JSON 형식을 찾지 못했습니다.")

    return {
        "count": len(results),
        "results": results,
    }


def get_sample_runtime_json(runtime_type: str = "cloud_run") -> dict[str, Any]:
    """Returns a realistic sample JSON payload so users can test the paste-and-analyze workflow with 1 click."""
    if runtime_type == "agent_runtime":
        return {
            "name": "projects/geap-sandbox-kwlee/locations/us-central1/reasoningEngines/2584329788197961728",
            "displayName": "Graph Workflow (ADK Agent Runtime)",
            "spec": {
                "agentFramework": "google-adk",
                "serviceAccount": "sa-graph-workflow@geap-sandbox-kwlee.iam.gserviceaccount.com",
                "deploymentSpec": {
                    "minInstances": 1,
                    "maxInstances": 10,
                    "env": [
                        {"name": "GOOGLE_CLOUD_LOCATION", "value": "global"},
                        {"name": "GOOGLE_GENAI_USE_ENTERPRISE", "value": "true"},
                        {"name": "ADK_SESSION_SERVICE_URI", "value": "agentengine://"},
                        {"name": "OTEL_SEMCONV_STABILITY_OPT_IN", "value": "gen_ai_latest_experimental"},
                        {"name": "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "value": "EVENT_ONLY"},
                    ],
                },
            },
            "createTime": "2026-10-27T02:33:29Z",
        }
    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": "currency-agent",
            "labels": {
                "cloud.googleapis.com/location": "us-central1",
                "created-by": "adk",
            },
            "annotations": {
                "run.googleapis.com/ingress": "all",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "run.googleapis.com/startup-cpu-boost": "true",
                    },
                    "labels": {
                        "created-by": "adk",
                    },
                },
                "spec": {
                    "containerConcurrency": 80,
                    "serviceAccountName": "getting-started-mcp-a2a-adk@geap-sandbox-kwlee.iam.gserviceaccount.com",
                    "containers": [
                        {
                            "image": "us-central1-docker.pkg.dev/geap-sandbox-kwlee/cloud-run-source-deploy/currency-agent:latest",
                            "ports": [{"containerPort": 8000, "name": "http1"}],
                            "resources": {"limits": {"cpu": "1000m", "memory": "512Mi"}},
                            "env": [
                                {"name": "GOOGLE_GENAI_USE_ENTERPRISE", "value": "true"},
                                {"name": "GOOGLE_CLOUD_PROJECT", "value": "geap-sandbox-kwlee"},
                                {"name": "GOOGLE_CLOUD_LOCATION", "value": "global"},
                                {"name": "MCP_SERVER_URL", "value": "https://currency-mcp-server-r6j5nyqrla-uc.a.run.app/mcp"},
                            ],
                            "startupProbe": {"tcpSocket": {"port": 8000}},
                        }
                    ],
                },
            }
        },
        "status": {
            "url": "https://currency-agent-r6j5nyqrla-uc.a.run.app",
        },
    }


def _evaluate_cloud_run_service(
    project_id: str,
    region: str,
    service_name: str,
    svc: dict[str, Any],
    now_str: str,
) -> dict[str, Any]:
    meta = svc.get("metadata", {})
    meta_ann = meta.get("annotations", {})
    labels = meta.get("labels", {})
    spec = svc.get("spec", {})
    tmpl = spec.get("template", {})
    tmpl_meta = tmpl.get("metadata", {})
    tmpl_ann = tmpl_meta.get("annotations", {})
    tmpl_spec = tmpl.get("spec", {})
    containers = tmpl_spec.get("containers", [{}])
    container = containers[0] if containers else {}

    status_obj = svc.get("status", {})
    service_url = status_obj.get("url", "")
    created_by = labels.get("created-by", meta_ann.get("run.googleapis.com/client-name", "gcloud"))

    ingress = meta_ann.get("run.googleapis.com/ingress", "all")
    min_scale = int(tmpl_ann.get("autoscaling.knative.dev/minScale", meta_ann.get("run.googleapis.com/minScale", "0")))
    max_scale = int(tmpl_ann.get("autoscaling.knative.dev/maxScale", meta_ann.get("run.googleapis.com/maxScale", "100")))
    cpu_boost = tmpl_ann.get("run.googleapis.com/startup-cpu-boost", "false").lower() == "true"
    cpu_throttling = tmpl_ann.get("run.googleapis.com/cpu-throttling", "true").lower() == "true"
    exec_env = tmpl_ann.get("run.googleapis.com/execution-environment", "default (미지정)")
    vpc_access = tmpl_ann.get("run.googleapis.com/vpc-access-connector", "") or tmpl_ann.get("run.googleapis.com/network-interfaces", "")
    vpc_egress = tmpl_ann.get("run.googleapis.com/vpc-access-egress", "")
    cloudsql_instances = tmpl_ann.get("run.googleapis.com/cloudsql-instances", "")

    concurrency = int(tmpl_spec.get("containerConcurrency", 80))
    timeout_sec = int(tmpl_spec.get("timeoutSeconds", 300))
    sa_email = tmpl_spec.get("serviceAccountName", "")
    limits = container.get("resources", {}).get("limits", {})
    cpu_str = limits.get("cpu", "1000m")
    mem_str = limits.get("memory", "512Mi")
    cpu_cores = _parse_cpu_cores(cpu_str)
    mem_mib = _parse_memory_mib(mem_str)

    env_list = container.get("env", [])
    env_map: dict[str, str] = {}
    for e in env_list:
        k = e.get("name", "")
        if "value" in e:
            env_map[k] = str(e.get("value", ""))

    startup_probe = container.get("startupProbe", {})
    liveness_probe = container.get("livenessProbe", {})
    has_http_liveness = "httpGet" in liveness_probe
    has_http_startup = "httpGet" in startup_probe
    port_num = container.get("ports", [{"containerPort": 8080}])[0].get("containerPort", 8080)

    checks: list[dict[str, Any]] = []

    # 1. [R1.1] Scaling & Cold Start / CPU Allocation (Workload / SLA Trade-off)
    if min_scale >= 1 and cpu_boost and not cpu_throttling:
        r11_status, r11_score = "GREEN", 100
        r11_current = f"minScale={min_scale}, maxScale={max_scale}, startup-cpu-boost=true, cpu-throttling=false (항시 Warm)"
    else:
        r11_status, r11_score = "REVIEW", None
        r11_current = (
            f"minScale={min_scale}, maxScale={max_scale}, "
            f"startup-cpu-boost={str(cpu_boost).lower()}, cpu-throttling={str(cpu_throttling).lower()}"
        )

    checks.append({
        "id": "R1.1",
        "domain": "1. 런타임 & 스케일링",
        "check_type": "WORKLOAD_TUNING",
        "verification_scope": "WORKLOAD_TUNING",
        "title": "스케일링 · 콜드스타트 · CPU 할당 정책 (SLA vs 비용 트레이드오프)",
        "status": r11_status,
        "score": r11_score,
        "current_state": r11_current,
        "recommended_state": "트래픽 패턴·지연 SLA에 따라 결정 (간헐 트래픽: min=0 + cpu-boost / 상시 SLA·비동기 후처리: min>=1 + no-cpu-throttling)",
        "why_it_matters": (
            "minScale=0 + startup-cpu-boost=true 조합은 트래픽이 간헐적이거나 유휴 비용을 줄여야 하는 환경에서 합리적인 설정입니다. "
            "반면 첫 응답 지연 SLA가 엄격하거나 응답 반환 후 백그라운드 작업(OTel 비동기 flush 등)이 실행된다면 "
            "min-instances >= 1 및 --no-cpu-throttling(CPU Always-On)을 검토하세요."
        ),
        "how_to_fix_title": "원하는 스케일링 정책 적용 (SLA·예산에 맞게 숫자 조정)",
        "gcloud_cmd": (
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --min-instances={max(1, min_scale)} \\\n"
            f"  --max-instances={max_scale} \\\n"
            f"  --cpu-boost \\\n"
            f"  --no-cpu-throttling"
        ),
        "terraform_snippet": (
            f'scaling {{\n  min_instance_count = {max(1, min_scale)}\n  max_instance_count = {max_scale}\n}}\n'
            'containers {\n  resources {\n    cpu_idle          = false\n    startup_cpu_boost = true\n  }\n}'
        ),
        "console_guide": f"Cloud Run 콘솔 > {service_name} > [새 버전 수정 및 배포]에서 서비스 SLA와 유휴 비용 예산에 맞춰 최소/최대 인스턴스 및 CPU 할당 모드를 결정하세요.",
    })

    # 2. [R1.2] Memory / CPU / Concurrency Sizing (Must be determined by load testing!)
    is_default_sizing = (abs(cpu_cores - 1.0) < 0.01 and mem_mib == 512 and concurrency == 80)
    r12_status, r12_score = "REVIEW", None
    if is_default_sizing:
        r12_current = f"CPU={cpu_str}, Memory={mem_str}, Concurrency={concurrency}, Timeout={timeout_sec}s (Cloud Run 기본값 그대로 사용 중)"
    else:
        r12_current = f"CPU={cpu_str}, Memory={mem_str}, Concurrency={concurrency}, Timeout={timeout_sec}s (사용자 커스텀 설정됨)"

    checks.append({
        "id": "R1.2",
        "domain": "1. 런타임 & 스케일링",
        "check_type": "WORKLOAD_TUNING",
        "verification_scope": "WORKLOAD_TUNING",
        "title": "CPU · 메모리 · 동시성(Concurrency) 사이징 (성능 테스트 기반 산정 필요)",
        "status": r12_status,
        "score": r12_score,
        "current_state": r12_current,
        "recommended_state": "에이전트별 부하 테스트(실제 메모리 사용량·P95 지연·동시 요청 수) 측정 후 직접 결정 (임의 고정값 권장 불가)",
        "why_it_matters": (
            f"JSON만으로는 {service_name}의 실제 피크 메모리·CPU 사용량을 알 수 없습니다. "
            "경량 API 래퍼형 에이전트는 512Mi~1Gi로도 충분할 수 있고, 대용량 문서·로컬 캐시·다수 동시 스트리밍을 처리하면 더 큰 메모리가 필요합니다. "
            "다만 현재 값이 Cloud Run 기본값(512Mi / 동시성 80)이라면, 실제 예상 트래픽으로 부하 테스트를 수행해 "
            "OOM이나 스레드 경합이 없는 적정 수치를 확정하세요."
        ),
        "how_to_fix_title": "부하 테스트로 확정한 CPU / Memory / Concurrency 값 반영",
        "gcloud_cmd": (
            f"# ⚠️ 아래 수치는 예시이며, 부하 테스트 결과에 맞춰 직접 수정 후 실행하세요\n"
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --cpu={int(cpu_cores) if cpu_cores >= 1 else cpu_str} \\\n"
            f"  --memory={mem_str} \\\n"
            f"  --concurrency={concurrency}"
        ),
        "terraform_snippet": (
            f'max_instance_request_concurrency = {concurrency}\n'
            f'containers {{\n  resources {{\n    limits = {{\n      cpu    = "{cpu_str}"\n      memory = "{mem_str}"\n    }}\n  }}\n}}'
        ),
        "console_guide": f"Cloud Monitoring에서 {service_name}의 메모리/CPU 사용률과 컨테이너 인스턴스 수를 확인한 뒤, 부하 테스트 결과에 맞춰 적정 스펙을 설정하세요.",
    })

    # 3. [R1.3] Health Probes & Execution Environment
    if has_http_liveness:
        r13_status, r13_score = "GREEN", 100
        liveness_path = liveness_probe.get("httpGet", {}).get("path", "/")
        r13_current = f"HTTP Liveness Probe 설정됨 ({liveness_path}:{port_num}), 실행 환경: {exec_env}"
    else:
        r13_status, r13_score = "REVIEW", None
        probe_desc = "HTTP Startup Probe" if has_http_startup else f"TCP Startup Probe (port {port_num})"
        r13_current = f"{probe_desc}만 사용 중 (HTTP Liveness Probe 미설정, 실행 환경: {exec_env})"

    checks.append({
        "id": "R1.3",
        "domain": "1. 런타임 & 스케일링",
        "check_type": "WORKLOAD_TUNING",
        "verification_scope": "WORKLOAD_TUNING",
        "title": "HTTP Liveness 헬스 프로브 & 실행 환경(Gen2) 확인",
        "status": r13_status,
        "score": r13_score,
        "current_state": r13_current,
        "recommended_state": "경량 HTTP 헬스 엔드포인트(/health 또는 /list-apps)가 있는 경우 HTTP Liveness Probe 추가 검토",
        "why_it_matters": (
            "기본 TCP Startup Probe는 컨테이너 시작 시 포트 오픈 여부만 확인합니다. "
            "에이전트 서버에 경량 HTTP 헬스체크 경로(예: ADK 기본 /list-apps 또는 커스텀 /health)가 구현되어 있다면, "
            "HTTP Liveness Probe를 추가해 이벤트 루프 데드락 시 자동 재시작하도록 구성할 수 있습니다. (엔드포인트가 없다면 추가 전 먼저 경로 구현 필요)"
        ),
        "how_to_fix_title": "HTTP Liveness 프로브 경로 확인 후 설정 (선택)",
        "gcloud_cmd": (
            f"# ⚠️ 컨테이너에 해당 HTTP 경로가 실제 존재하는지 확인 후 실행하세요\n"
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --execution-environment=gen2 \\\n"
            f"  --liveness-probe=httpGet.path=/list-apps,httpGet.port={port_num},periodSeconds=30"
        ),
        "terraform_snippet": (
            'execution_environment = "EXECUTION_ENVIRONMENT_GEN2"\n'
            'liveness_probe {\n  http_get {\n    path = "/list-apps"\n  }\n  period_seconds = 30\n}'
        ),
        "console_guide": f"에이전트가 제공하는 헬스체크 HTTP 경로를 확인한 뒤 Cloud Run 콘솔 > {service_name} > [컨테이너] > 상태 확인에서 Liveness 프로브를 추가하세요.",
    })

    # 4. [R2.1] Session & Memory Persistence (Cannot inspect Python code from describe JSON!)
    session_uri = env_map.get("ADK_SESSION_SERVICE_URI") or env_map.get("SESSION_SERVICE_URI") or ""
    agent_engine_ref = (
        env_map.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
        or env_map.get("AGENT_ENGINE_ID")
        or env_map.get("REASONING_ENGINE_ID")
        or ""
    )
    has_vertex_session = "agentengine://" in session_uri.lower() or bool(agent_engine_ref)
    has_db = bool(cloudsql_instances or env_map.get("INSTANCE_CONNECTION_NAME") or (session_uri and not has_vertex_session))
    has_redis = bool(env_map.get("REDIS_HOST"))

    if has_vertex_session:
        r21_status, r21_score = "GREEN", 100
        r21_current = f"Agent Platform Managed Session Service / Memory Bank 연동 감지됨 ({session_uri or agent_engine_ref})"
    elif has_db and has_redis:
        r21_status, r21_score = "GREEN", 100
        r21_current = f"Self-Hosted Cloud SQL ({cloudsql_instances or env_map.get('INSTANCE_CONNECTION_NAME') or session_uri}) + Redis ({env_map.get('REDIS_HOST')}) 연동됨"
    elif has_db:
        r21_status, r21_score = "GREEN", 100
        r21_current = f"Self-Hosted 외부 세션 DB 연동 감지됨 ({cloudsql_instances or env_map.get('INSTANCE_CONNECTION_NAME') or session_uri})"
    else:
        r21_status, r21_score = "REVIEW", None
        r21_current = (
            "Cloud Run JSON에서 Managed Session(GOOGLE_CLOUD_AGENT_ENGINE_ID / ADK_SESSION_SERVICE_URI) 미감지 "
            "(※ 단, 코드 내부 연동 또는 Stateless 단일턴 여부는 JSON만으로 판별 불가)"
        )

    checks.append({
        "id": "R2.1",
        "domain": "2. 세션 & 상태 영속성",
        "check_type": "ARCH_DECISION",
        "verification_scope": "ARCH_DECISION",
        "title": "멀티턴 대화 영속성 (기본: Agent Platform Managed Sessions & Memory Bank)",
        "status": r21_status,
        "score": r21_score,
        "current_state": r21_current,
        "recommended_state": (
            "멀티턴 운영 기본 권장: Agent Platform Managed Session Service & Memory Bank (`GOOGLE_CLOUD_AGENT_ENGINE_ID` 연동) / "
            "Stateless 단일턴이면 불필요 / (참고 옵션: Self-Hosted Cloud SQL·Redis·Firestore 직접 구축)"
        ),
        "why_it_matters": (
            "`gcloud run services describe` JSON만으로는 컨테이너 Python 코드 내부에서 Agent Platform Managed Session Service·Memory Bank를 호출하는지, "
            "아니면 Stateless 에이전트인지 판별할 수 없습니다. 멀티턴 대화가 필요한데 `InMemorySessionService`만 사용 중이라면 인스턴스 스케일아웃·재시작 시 대화가 유실되므로, "
            "별도 DB 운영 부담이 없는 **Agent Platform Managed Sessions & Memory Bank**(`GOOGLE_CLOUD_AGENT_ENGINE_ID`) 연동을 기본 권장합니다. "
            "(사내 규제나 기존 인프라 재사용이 필요한 경우 Self-Hosted Cloud SQL / Memorystore Redis / AlloyDB를 직접 구성할 수도 있습니다.)"
        ),
        "how_to_fix_title": "Agent Platform Managed Sessions & Memory Bank 환경변수 연결",
        "gcloud_cmd": (
            f"# [기본 권장 · Managed] Agent Platform Session Service & Memory Bank 연결\n"
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --update-env-vars=\"GOOGLE_CLOUD_AGENT_ENGINE_ID=<AGENT_ENGINE_ID>\"\n\n"
            f"# (참고: Self-Hosted Cloud SQL을 직접 운영할 경우 --add-cloudsql-instances 옵션 사용 가능)"
        ),
        "terraform_snippet": (
            '# 기본 권장: Agent Platform Managed Session & Memory Bank\n'
            'env {\n  name  = "GOOGLE_CLOUD_AGENT_ENGINE_ID"\n  value = google_vertex_ai_reasoning_engine.memory_engine.name\n}'
        ),
        "console_guide": "멀티턴 운영 시 `GOOGLE_CLOUD_AGENT_ENGINE_ID`를 연결해 Managed Session & Memory Bank를 사용하세요. (Stateless이거나 이미 Self-Hosted DB를 운영 중이라면 그대로 유지 가능)",
    })

    # 5. [R3.1] Inbound Ingress Exposure & Auth Check (Public vs Internal depends on intent!)
    if ingress in ("internal", "internal-and-cloud-load-balancing"):
        r31_status, r31_score = "GREEN", 100
        r31_current = f"ingress = {ingress} (내부 VPC 또는 Cloud Load Balancing 경유 트래픽만 허용)"
    else:
        r31_status, r31_score = "REVIEW", None
        r31_current = (
            f"ingress = {ingress} (퍼블릭 .run.app 엔드포인트 열림 — "
            "※ 퍼블릭/외부 A2A 에이전트라면 정상 의도이며, IAM 무인증(allUsers) 여부는 별도 get-iam-policy로 확인 필요)"
        )

    checks.append({
        "id": "R3.1",
        "domain": "3. 네트워크 & 엣지 보안",
        "check_type": "ARCH_DECISION",
        "verification_scope": "ARCH_DECISION",
        "title": "인바운드 노출 범위(Public vs Internal) 및 IAM 인증 정책 확인",
        "status": r31_status,
        "score": r31_score,
        "current_state": r31_current,
        "recommended_state": "퍼블릭/외부 A2A 서비스: ingress=all + IAM 인증(또는 앱 인증) 검증 / 사내 전용·WAF 경유: internal 또는 internal-and-cloud-load-balancing",
        "why_it_matters": (
            f"현재 {service_name}는 `ingress: {ingress}` 상태입니다. 외부 고객이나 외부 A2A 클라이언트가 직접 호출하는 퍼블릭 에이전트라면 `ingress: all`이 의도된 정상 구성입니다. "
            "다만 `describe` JSON에는 IAM 바인딩(`allUsers` 허용 여부)이 포함되지 않으므로, 무인증 호출이 의도된 것인지 아래 `get-iam-policy` 명령으로 반드시 별도 확인하세요. "
            "사내 전용이거나 Cloud Armor WAF/LB 뒤에만 배치할 에이전트인 경우에만 인그레스 제한을 적용하세요."
        ),
        "how_to_fix_title": "1) 현재 IAM 노출 여부 조회 / 2) 사내·LB 전용일 때만 Ingress 제한",
        "gcloud_cmd": (
            f"# [Step 1 · 읽기 전용 확인] allUsers(무인증 공개) 포함 여부 점검:\n"
            f"gcloud run services get-iam-policy {service_name} --region={region} --project={project_id}\n\n"
            f"# [Step 2 · 사내 전용 또는 Cloud Armor LB 경유 에이전트인 경우에만 실행]\n"
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --ingress=internal-and-cloud-load-balancing"
        ),
        "terraform_snippet": 'ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" # 사내/LB 전용 아키텍처일 때 적용',
        "console_guide": f"퍼블릭 에이전트라면 Cloud Run 콘솔 > {service_name} > [보안] 탭에서 인증 설정(IAM 필요 vs 공개 허용)이 의도와 일치하는지만 확인하세요.",
    })

    # 6. [R3.2] Outbound VPC Egress (Only needed if calling private VPC resources!)
    public_url_envs = [f"{k}={v}" for k, v in env_map.items() if "URL" in k.upper() and v.startswith("https://")]
    if vpc_access:
        r32_status, r32_score = "GREEN", 100
        r32_current = f"Direct VPC / 커넥터 연결됨 ({vpc_egress or 'private-ranges-only'})"
    else:
        r32_status, r32_score = "REVIEW", None
        hint = f" (감지된 아웃바운드 URL: {', '.join(public_url_envs[:2])})" if public_url_envs else ""
        r32_current = f"Direct VPC Egress 미연결 — 퍼블릭 API/Gemini/퍼블릭 MCP만 호출한다면 불필요{hint}"

    checks.append({
        "id": "R3.2",
        "domain": "3. 네트워크 & 엣지 보안",
        "check_type": "ARCH_DECISION",
        "verification_scope": "ARCH_DECISION",
        "title": "아웃바운드 Direct VPC Egress 필요 여부 확인 (사설 IP 리소스 호출 시에만 필요)",
        "status": r32_status,
        "score": r32_score,
        "current_state": r32_current,
        "recommended_state": "퍼블릭 API·Gemini·퍼블릭 Cloud Run만 호출: 미연결 유지 권장 / 사설 VPC DB·내부 API 호출: Direct VPC Egress 연결",
        "why_it_matters": (
            "에이전트가 Gemini API, 퍼블릭 외부 API, 또는 퍼블릭 Cloud Run MCP 서버 등 공용 HTTPS 엔드포인트만 호출한다면 Direct VPC Egress는 전혀 필요하지 않으며, "
            "불필요하게 연결하면 서브넷 IP만 소모됩니다. 반면 사설 IP(RFC1918)를 쓰는 Cloud SQL, Memorystore Redis, 온프레미스 API, "
            "또는 `ingress: internal` 백엔드 서비스와 통신해야 한다면 Direct VPC Egress를 연결하세요."
        ),
        "how_to_fix_title": "사설 VPC 리소스 접근이 필요한 경우에만 Direct VPC Egress 연결",
        "gcloud_cmd": (
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --network=default \\\n"
            f"  --subnet=default \\\n"
            f"  --vpc-egress=private-ranges-only"
        ),
        "terraform_snippet": (
            'vpc_access {\n'
            '  egress = "PRIVATE_RANGES_ONLY"\n'
            '  network_interfaces {\n'
            '    network    = "default"\n'
            '    subnetwork = "default"\n'
            '  }\n}'
        ),
        "console_guide": "사설 VPC 자원 접근이 없다면 조치할 필요가 없습니다. 사설망 통신이 필요할 때만 [네트워킹] 탭에서 Direct VPC를 연결하세요.",
    })

    # 7. [R4.1] Dedicated Least-Privilege Service Account (Objectively verifiable from JSON!)
    is_default_compute_sa = "-compute@developer.gserviceaccount.com" in sa_email or not sa_email
    if not is_default_compute_sa:
        r41_status, r41_score = "GREEN", 100
        r41_current = f"전용 서비스 계정 사용 중 ({sa_email})"
    else:
        r41_status, r41_score = "RED", 0
        r41_current = f"기본 Compute Engine 서비스 계정 사용 중 ({sa_email or 'default'}) — 과다 권한 위험 (JSON에서 직접 확인됨)"

    checks.append({
        "id": "R4.1",
        "domain": "4. 보안 & 가드레일",
        "check_type": "RUNTIME_VERIFIED",
        "verification_scope": "RUNTIME_VERIFIED",
        "title": "전용 최소 권한 서비스 계정 (Dedicated IAM Service Account)",
        "status": r41_status,
        "score": r41_score,
        "current_state": r41_current,
        "recommended_state": f"에이전트 전용 서비스 계정 사용 + 필요한 최소 IAM Role만 부여",
        "why_it_matters": (
            "Cloud Run `describe` JSON의 `serviceAccountName` 필드로 객관적 검증이 가능한 항목입니다. "
            "기본 Compute 서비스 계정은 프로젝트 전체에 광범위한 권한이 부여되어 있는 경우가 많으므로, "
            "전용 서비스 계정을 분리하고 최소 권한(Agent Platform User 등 필요한 역할만)을 부여하는 것이 안전합니다."
        ),
        "how_to_fix_title": "전용 서비스 계정 생성 및 연결",
        "gcloud_cmd": (
            f"gcloud iam service-accounts create {service_name}-sa --project={project_id}\n"
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --service-account={service_name}-sa@{project_id}.iam.gserviceaccount.com"
        ),
        "terraform_snippet": "service_account = google_service_account.app_sa.email",
        "console_guide": f"전용 SA가 이미 연결되어 있더라도 `gcloud projects get-iam-policy {project_id}`로 해당 SA에 과도한 역할(Editor/Owner 등)이 없는지 주기적으로 점검하세요.",
    })

    # 8. [R4.2] Model Armor Guardrail & Tool Security (Cannot inspect Python code from describe JSON!)
    has_guardrail_env = env_map.get("MODEL_ARMOR_ENABLED", "").lower() == "true" or "MODEL_ARMOR_TEMPLATE_ID" in env_map
    if has_guardrail_env:
        r42_status, r42_score = "GREEN", 100
        r42_current = "Model Armor 관련 환경변수 감지됨 (MODEL_ARMOR_ENABLED / TEMPLATE_ID)"
    else:
        r42_status, r42_score = "REVIEW", None
        r42_current = "런타임 JSON에서 Model Armor 환경변수 미감지 (※ 코드 내부 콜백/SDK 직접 호출 또는 앞단 Agent Gateway 적용 여부는 JSON으로 판별 불가)"

    checks.append({
        "id": "R4.2",
        "domain": "4. 보안 & 가드레일",
        "check_type": "CODE_VERIFY",
        "verification_scope": "CODE_VERIFY",
        "title": "AI 보안 거버넌스 (Agent Gateway · Model Armor) — 선택 거버넌스 확인",
        "status": r42_status,
        "score": r42_score,
        "current_state": r42_current,
        "recommended_state": "조직 AI 보안 거버넌스 정책에 따라 앞단 Agent Gateway + Model Armor Floor Settings로 중앙 통제하거나, 필요한 에이전트에만 선택적 ADK ModelArmorPlugin 적용",
        "why_it_matters": (
            "Cloud Run `describe` JSON은 앞단 Agent Gateway(Model Armor Floor Settings)나 컨테이너 내부 콜백의 보안 정책을 노출하지 않습니다. "
            "모든 에이전트 코드에 일괄 강제하기보다 조직의 AI 보안 거버넌스 기준에 맞춰 게이트웨이 또는 앱 레벨 적용 여부를 결정하세요."
        ),
        "how_to_fix_title": "Agent Gateway + Model Armor Floor Settings 또는 선택적 ADK ModelArmorPlugin 검토",
        "gcloud_cmd": (
            f"# [선택 거버넌스] 조직/프로젝트 단위 Model Armor Floor Settings 또는 Agent Gateway 정책 확인\n"
            f"gcloud model-armor floorsettings describe --project={project_id} --location=global\n"
            f"# 로컬 코드 전체 점검 (13개 No-Regret 항목):\n"
            f"uv run prod-ready scan <내_로컬_에이전트_폴더>"
        ),
        "terraform_snippet": (
            '# 권장: Agent Gateway + Model Armor Floor Settings로 중앙 거버넌스 통제\n'
            '# (또는 필요한 서비스에서만 ADK before_model_callback / ModelArmorPlugin 선택 장착)'
        ),
        "console_guide": "이 항목은 점수에 반영되지 않는 거버넌스 참고(REVIEW) 항목입니다. 조직 보안 정책상 필요한 경우에만 Model Armor / Agent Gateway를 연동하세요.",
    })

    # 9. [R5.1] OpenTelemetry Prompt Privacy Guard (NO_CONTENT)
    otel_val = env_map.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "")
    otel_semconv = "OTEL_SEMCONV_STABILITY_OPT_IN" in env_map
    if otel_val == "NO_CONTENT":
        r51_status, r51_score = "GREEN", 100
        r51_current = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT 설정됨 (프롬프트 평문 기록 차단)"
    elif otel_val:
        r51_status, r51_score = "AMBER", 50
        r51_current = f"OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT={otel_val} (프로덕션 PII 취급 시 NO_CONTENT 변경 권고)"
    else:
        r51_status, r51_score = "AMBER", 50
        r51_current = (
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT 미설정 "
            "(OTel GenAI 트레이싱 활성화 시 프롬프트 원문이 기록될 수 있어 NO_CONTENT 명시 권고)"
        )

    checks.append({
        "id": "R5.1",
        "domain": "5. 관측성 & 프라이버시",
        "check_type": "RUNTIME_RECOMMENDED",
        "verification_scope": "RUNTIME_VERIFIED",
        "title": "OpenTelemetry 트레이스 프롬프트(PII) 평문 기록 방지 (NO_CONTENT)",
        "status": r51_status,
        "score": r51_score,
        "current_state": r51_current,
        "recommended_state": "프로덕션 OTel 트레이싱 사용 시 OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT 권장",
        "why_it_matters": (
            "분산 트레이싱(Cloud Trace)을 켠 운영 환경에서 개인정보가 포함된 사용자 프롬프트 원문이 트레이스에 평문 저장되는 것을 방지하기 위한 안전장치입니다. "
            "(트레이싱을 사용하지 않거나 프롬프트 디버깅이 필요한 개발/샌드박스 환경이라면 설정하지 않아도 무방합니다.)"
        ),
        "how_to_fix_title": "OTel 프라이버시 환경변수(NO_CONTENT) 명시 설정",
        "gcloud_cmd": (
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --update-env-vars=\"OTEL_SERVICE_NAME={service_name},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental\""
        ),
        "terraform_snippet": (
            'env {\n  name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\n  value = "NO_CONTENT"\n}\n'
            'env {\n  name  = "OTEL_SEMCONV_STABILITY_OPT_IN"\n  value = "gen_ai_latest_experimental"\n}'
        ),
        "console_guide": f"Cloud Run 콘솔 > {service_name} > [변수 및 보안 비밀] 탭에서 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT`를 추가하세요.",
    })

    # 10. [R6.1] Gemini Endpoint Location (Global DSQ vs Regional Data Residency)
    loc_val = env_map.get("GOOGLE_CLOUD_LOCATION", "")
    is_global_loc = loc_val.lower() == "global"
    if is_global_loc:
        r61_status, r61_score = "GREEN", 100
        r61_current = "GOOGLE_CLOUD_LOCATION=global 설정 확인됨 (Global Dynamic Shared Quota 활용)"
    elif loc_val:
        r61_status, r61_score = "REVIEW", None
        r61_current = f"GOOGLE_CLOUD_LOCATION={loc_val} (단일 리전 고정 — 데이터 레지던시 규제 때문이라면 정상, 쿼터 분산이 우선이면 global 검토)"
    else:
        r61_status, r61_score = "AMBER", 50
        r61_current = "GOOGLE_CLOUD_LOCATION 환경변수 미감지 (코드 기본값 확인 또는 global 엔드포인트 지정 권장)"

    checks.append({
        "id": "R6.1",
        "domain": "6. FinOps 비용 & 쿼터 회복력",
        "check_type": "RUNTIME_VERIFIED" if is_global_loc else ("ARCH_DECISION" if loc_val else "RUNTIME_RECOMMENDED"),
        "verification_scope": "RUNTIME_VERIFIED" if (is_global_loc or not loc_val) else "ARCH_DECISION",
        "title": "Gemini 엔드포인트 위치 (Global DSQ vs 데이터 레지던시)",
        "status": r61_status,
        "score": r61_score,
        "current_state": r61_current,
        "recommended_state": "데이터 레지던시 제약이 없다면 GOOGLE_CLOUD_LOCATION=global (429 쿼터 분산) + 코드 내 루프 상한 확인(Play 01)",
        "why_it_matters": (
            "`GOOGLE_CLOUD_LOCATION=global`을 사용하면 Google Cloud의 Global Dynamic Shared Quota(DSQ)를 통해 429 오류를 줄일 수 있습니다. "
            "단, 특정 국가/리전 데이터 레지던시(예: `asia-northeast3`, `europe-west4`) 준수가 필수인 규제 환경에서는 의도적으로 리전을 고정해야 합니다. "
            "(※ 에이전트 무한 도구 루프 방지 및 토큰 상한 로직은 `describe` JSON에서 보이지 않으므로 Play 01 소스 코드에서 확인하세요.)"
        ),
        "how_to_fix_title": "Global 엔드포인트 지정 (데이터 레지던시 제약이 없는 경우)",
        "gcloud_cmd": (
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --update-env-vars=\"GOOGLE_CLOUD_LOCATION=global\""
        ),
        "terraform_snippet": 'env {\n  name  = "GOOGLE_CLOUD_LOCATION"\n  value = "global"\n}',
        "console_guide": f"Cloud Run 콘솔 > {service_name} > [변수 및 보안 비밀]에서 `GOOGLE_CLOUD_LOCATION` 값을 서비스의 레지던시·쿼터 정책에 맞게 설정하세요.",
    })

    verified_checks = [c for c in checks if c["status"] in ("GREEN", "AMBER", "RED")]
    green_cnt = sum(1 for c in checks if c["status"] == "GREEN")
    amber_cnt = sum(1 for c in checks if c["status"] == "AMBER")
    red_cnt = sum(1 for c in checks if c["status"] == "RED")
    review_cnt = sum(1 for c in checks if c["status"] == "REVIEW")

    total_score = (
        round(sum(c["score"] for c in verified_checks if c["score"] is not None) / len(verified_checks), 1)
        if verified_checks
        else 100.0
    )

    if red_cnt > 0:
        verdict = f"🔴 객관적 위험 {red_cnt}건 발견 (우선 조치 권장)"
    elif amber_cnt > 0:
        verdict = f"🟡 검증 항목 양호 · 선택 권고 {amber_cnt}건 / 사용자 확인 {review_cnt}건"
    elif review_cnt > 0:
        verdict = f"🔵 JSON 검증 항목 모두 통과 · 아키텍처/성능 {review_cnt}건 확인 필요"
    else:
        verdict = "🟢 Production Ready (모든 항목 검증 완료)"

    combined_gcloud = (
        f"# ⚠️ 선택한 항목에 따라 UI에서 맞춤형 명령어가 동적으로 생성됩니다.\n"
        f"gcloud run services update {service_name} \\\n"
        f"  --region={region} \\\n"
        f"  --project={project_id}"
    )

    return {
        "project_id": project_id,
        "region": region,
        "service_name": service_name,
        "runtime_type": "cloud_run",
        "runtime_label": "Cloud Run Service",
        "service_url": service_url,
        "created_by": created_by,
        "service_account": sa_email,
        "inspected_at": now_str,
        "total_score": total_score,
        "verdict": verdict,
        "green_count": green_cnt,
        "amber_count": amber_cnt,
        "red_count": red_cnt,
        "review_count": review_cnt,
        "verified_count": len(verified_checks),
        "detected_config": {
            "cpu": cpu_str,
            "memory": mem_str,
            "concurrency": concurrency,
            "min_scale": min_scale,
            "max_scale": max_scale,
            "timeout_seconds": timeout_sec,
            "cpu_boost": cpu_boost,
            "cpu_throttling": cpu_throttling,
            "ingress": ingress,
            "port": port_num,
            "execution_environment": exec_env,
        },
        "combined_runtime_fix_cmd": combined_gcloud,
        "stage1_skill_cmd": (
            "# Play 01 (배포 전 로컬 PC): 13개 핵심 No-Regret 항목(루프 방지, Non-Root, OTel 프라이버시 등) 점검 및 패치\n"
            "uv run prod-ready scan <내_로컬_에이전트_코드_폴더>"
        ),
        "checks": checks,
    }


def _evaluate_agent_runtime_engine(
    project_id: str,
    region: str,
    engine_id: str,
    eng: dict[str, Any],
    now_str: str,
) -> dict[str, Any]:
    spec = eng.get("spec", {})
    dep_spec = spec.get("deploymentSpec", {})
    sa_email = spec.get("serviceAccount") or spec.get("effectiveIdentity", "")
    disp_name = eng.get("displayName") or engine_id
    psc_iface = dep_spec.get("pscInterfaceConfig", {})

    env_list = dep_spec.get("env", [])
    env_map = {e.get("name", ""): str(e.get("value", "")) for e in env_list if "name" in e}

    otel_capture = env_map.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "")
    loc_val = env_map.get("GOOGLE_CLOUD_LOCATION", "")
    is_global_loc = loc_val.lower() == "global"
    has_guardrail = env_map.get("MODEL_ARMOR_ENABLED", "").lower() == "true" or "MODEL_ARMOR_TEMPLATE_ID" in env_map
    min_inst = dep_spec.get("minInstances")
    max_inst = dep_spec.get("maxInstances")

    checks = [
        {
            "id": "R1.1",
            "domain": "1. 런타임 & 스케일링",
            "check_type": "MANAGED_NATIVE",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "Agent Runtime 관리형 스케일링 & 헬스체크 (플랫폼 자동 제공)",
            "status": "GREEN",
            "score": 100,
            "current_state": (
                f"Agent Runtime 관리형 컨테이너 ({spec.get('agentFramework', 'google-adk')})"
                + (f" · minInstances={min_inst}, maxInstances={max_inst}" if (min_inst is not None or max_inst is not None) else "")
            ),
            "recommended_state": "플랫폼 기본 관리형 제공 (minInstances / maxInstances는 SLA·트래픽에 맞게 조정)",
            "why_it_matters": "Agent Runtime은 인스턴스 오토스케일링과 컨테이너 헬스 프로브를 플랫폼이 자동으로 관리합니다.",
            "how_to_fix_title": "✅ 플랫폼 기본 제공 (추가 조치 불필요)",
            "gcloud_cmd": "# Agent Runtime 플랫폼이 자동으로 관리합니다.",
            "terraform_snippet": "# Managed by Agent Runtime",
            "console_guide": "조치 불필요 (필요 시 deploymentSpec의 minInstances/maxInstances만 트래픽에 맞춰 조정)",
        },
        {
            "id": "R2.1",
            "domain": "2. 세션 & 상태 영속성",
            "check_type": "MANAGED_NATIVE",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "Managed Session Service & Memory Bank 영속 세션",
            "status": "GREEN",
            "score": 100,
            "current_state": f"ADK_SESSION_SERVICE_URI={env_map.get('ADK_SESSION_SERVICE_URI', 'agentengine://')}, Agent Runtime 내장 세션/메모리 지원",
            "recommended_state": "agentengine:// 관리형 세션 및 Memory Bank 서비스",
            "why_it_matters": "Agent Runtime은 별도의 Cloud SQL이나 Redis 구축 없이 플랫폼 내장 Session Service와 Memory Bank를 기본 제공합니다.",
            "how_to_fix_title": "✅ 플랫폼 기본 제공 (추가 조치 불필요)",
            "gcloud_cmd": "# Managed Session Service & Memory Bank가 플랫폼에 내장되어 있습니다.",
            "terraform_snippet": "# Managed by Agent Runtime",
            "console_guide": "조치 불필요",
        },
        {
            "id": "R3.1",
            "domain": "3. 네트워크 & 엣지 보안",
            "check_type": "MANAGED_NATIVE",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "제로 트러스트 IAM 인바운드 인증 (퍼블릭 무인증 URL 미노출)",
            "status": "GREEN",
            "score": 100,
            "current_state": "Agent Platform IAM 인증 엔드포인트로만 호출 가능 (퍼블릭 무인증 노출 없음)",
            "recommended_state": "Agent Platform IAM Policy 강제 인증",
            "why_it_matters": "Agent Runtime은 공용 무인증 웹 URL을 노출하지 않고 IAM 권한이 있는 호출자만 허용합니다.",
            "how_to_fix_title": "✅ 플랫폼 기본 제공 (추가 조치 불필요)",
            "gcloud_cmd": "# Agent Platform IAM을 통해 인바운드가 보호됩니다.",
            "terraform_snippet": "# IAM protected by default",
            "console_guide": "조치 불필요",
        },
        {
            "id": "R3.2",
            "domain": "3. 네트워크 & 엣지 보안",
            "check_type": "ARCH_DECISION",
            "verification_scope": "ARCH_DECISION",
            "title": "Private Service Connect Interface (PSC-I) 필요 여부 확인 (사설 VPC 접근 시에만 필요)",
            "status": "GREEN" if psc_iface else "REVIEW",
            "score": 100 if psc_iface else None,
            "current_state": (
                f"PSC Interface 연결됨: {psc_iface.get('networkAttachment')}"
                if psc_iface
                else "PSC Interface 미연결 — 퍼블릭 API/Agent Platform만 호출한다면 불필요 (사내 프라이빗 DB/API 접근 시에만 필요)"
            ),
            "recommended_state": "사설 VPC/온프레미스 리소스 호출이 있는 경우에만 psc_interface_config.network_attachment 연결",
            "why_it_matters": (
                "Agent Runtime이 Gemini API나 외부 퍼블릭 HTTPS API만 호출한다면 PSC-I는 불필요합니다. "
                "고객사 VPC 내부의 프라이빗 DB, 사내 API, 온프레미스 자원에 사설 IP로 접근해야 하는 경우에만 PSC Network Attachment를 연결하세요."
            ),
            "how_to_fix_title": "사설 VPC 리소스 접근이 필요한 경우에만 PSC Network Attachment 연결",
            "gcloud_cmd": (
                f"uvx google-agents-cli deploy \\\n"
                f"  --project={project_id} \\\n"
                f"  --region={region} \\\n"
                f"  --network-attachment=projects/{project_id}/regions/{region}/networkAttachments/agent-psc"
            ),
            "terraform_snippet": 'psc_interface_config {\n  network_attachment = google_compute_network_attachment.agent_psc.id\n}',
            "console_guide": "사설망 통신이 필요 없다면 조치 불필요합니다. 필요 시 VPC Network Attachments 생성 후 배포에 연결하세요.",
        },
        {
            "id": "R4.1",
            "domain": "4. 보안 & 가드레일",
            "check_type": "RUNTIME_VERIFIED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "전용 최소 권한 서비스 계정 (Custom Service Account)",
            "status": "GREEN" if ("@gcp-sa-aiplatform-re" not in sa_email and sa_email) else "AMBER",
            "score": 100 if ("@gcp-sa-aiplatform-re" not in sa_email and sa_email) else 50,
            "current_state": f"현재 ID: {sa_email or '기본 Reasoning Engine 공유 SA'}",
            "recommended_state": "에이전트별 전용 커스텀 서비스 계정 할당",
            "why_it_matters": "여러 에이전트가 기본 Reasoning Engine 서비스 계정을 공유하면 에이전트별 최소 권한 분리가 어렵습니다.",
            "how_to_fix_title": "전용 커스텀 서비스 계정 지정",
            "gcloud_cmd": f"uvx google-agents-cli deploy --project={project_id} --region={region} --service-account=<CUSTOM_SA_EMAIL>",
            "terraform_snippet": "service_account = google_service_account.app_sa.email",
            "console_guide": "재배포 시 `--service-account` 플래그로 에이전트 전용 SA를 지정하세요.",
        },
        {
            "id": "R4.2",
            "domain": "4. 보안 & 가드레일",
            "check_type": "CODE_VERIFY",
            "verification_scope": "CODE_VERIFY",
            "title": "AI 보안 거버넌스 (Agent Gateway · Model Armor) — 선택 거버넌스 확인",
            "status": "GREEN" if has_guardrail else "REVIEW",
            "score": 100 if has_guardrail else None,
            "current_state": (
                "Model Armor 관련 환경변수 감지됨"
                if has_guardrail
                else "런타임 JSON에서 Model Armor 환경변수 미감지 (※ Agent Gateway / Floor Settings 중앙 통제 여부는 JSON으로 판별 불가)"
            ),
            "recommended_state": "조직 AI 보안 거버넌스 요건에 따라 Agent Gateway + Model Armor Floor Settings 또는 선택적 ADK ModelArmorPlugin 검토",
            "why_it_matters": "ReasoningEngine API JSON은 앞단 Agent Gateway 정책이나 코드 내부 가드레일을 노출하지 않습니다. 조직 거버넌스 정책상 필요한 경우에만 중앙 게이트웨이 또는 앱 플러그인으로 적용하세요.",
            "how_to_fix_title": "Model Armor Floor Settings 확인 및 Play 01 핵심 점검",
            "gcloud_cmd": f"gcloud model-armor floorsettings describe --project={project_id} --location=global\nuv run prod-ready scan <내_로컬_에이전트_폴더>",
            "terraform_snippet": "# 권장: Agent Gateway + Model Armor Floor Settings 중앙 정책",
            "console_guide": "점수에 반영되지 않는 거버넌스 참고(REVIEW) 항목입니다. 조직 정책에 따라 적용 여부를 결정하세요.",
        },
        {
            "id": "R5.1",
            "domain": "5. 관측성 & 프라이버시",
            "check_type": "RUNTIME_RECOMMENDED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "OpenTelemetry 트레이스 프롬프트(PII) 평문 기록 방지 (NO_CONTENT)",
            "status": "GREEN" if otel_capture == "NO_CONTENT" else "AMBER",
            "score": 100 if otel_capture == "NO_CONTENT" else 50,
            "current_state": f"OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = {otel_capture or '미설정'} (프로덕션 PII 취급 시 NO_CONTENT 권고)",
            "recommended_state": "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = NO_CONTENT",
            "why_it_matters": f"현재 `{otel_capture or '미설정'}` 상태입니다. 프로덕션에서 고객 개인정보가 포함된 프롬프트 원문이 트레이스에 기록되지 않도록 `NO_CONTENT` 설정을 권장합니다.",
            "how_to_fix_title": "OTel 프라이버시 환경변수 NO_CONTENT 설정",
            "gcloud_cmd": "uv run prod-ready apply <내_로컬_에이전트_폴더> --items C5.2",
            "terraform_snippet": 'env {\n  name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\n  value = "NO_CONTENT"\n}',
            "console_guide": "배포 설정에서 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 값을 `NO_CONTENT`로 지정하세요.",
        },
        {
            "id": "R6.1",
            "domain": "6. FinOps 비용 & 쿼터 회복력",
            "check_type": "RUNTIME_VERIFIED" if is_global_loc else ("ARCH_DECISION" if loc_val else "RUNTIME_RECOMMENDED"),
            "verification_scope": "RUNTIME_VERIFIED" if (is_global_loc or not loc_val) else "ARCH_DECISION",
            "title": "Gemini 엔드포인트 위치 (Global DSQ vs 데이터 레지던시)",
            "status": "GREEN" if is_global_loc else ("REVIEW" if loc_val else "AMBER"),
            "score": 100 if is_global_loc else (None if loc_val else 50),
            "current_state": (
                "GOOGLE_CLOUD_LOCATION=global 설정 확인됨 (Global Dynamic Shared Quota 활용)"
                if is_global_loc
                else (
                    f"GOOGLE_CLOUD_LOCATION={loc_val} (단일 리전 고정 — 데이터 레지던시 규제 때문이라면 정상)"
                    if loc_val
                    else "GOOGLE_CLOUD_LOCATION 환경변수 미감지 (코드 기본값 확인 또는 global 지정 권장)"
                )
            ),
            "recommended_state": "데이터 레지던시 제약이 없다면 GOOGLE_CLOUD_LOCATION=global + 코드 내 호출 상한 확인(Play 01)",
            "why_it_matters": "데이터 레지던시 요건이 없다면 Global DSQ(`global`)로 429 쿼터 오류를 완화할 수 있습니다. 무한 루프 방지 로직은 Play 01 소스 코드에서 확인하세요.",
            "how_to_fix_title": "Global 엔드포인트 지정 및 코드 내 비용 가드 점검",
            "gcloud_cmd": "uv run prod-ready apply <내_로컬_에이전트_폴더> --items C6.1,C6.2",
            "terraform_snippet": 'env {\n  name  = "GOOGLE_CLOUD_LOCATION"\n  value = "global"\n}',
            "console_guide": "데이터 레지던시·쿼터 정책에 맞춰 `GOOGLE_CLOUD_LOCATION`을 설정하고, 로컬 코드(Play 01)에서 루프 상한 가드를 확인하세요.",
        },
    ]

    verified_checks = [c for c in checks if c["status"] in ("GREEN", "AMBER", "RED")]
    green_cnt = sum(1 for c in checks if c["status"] == "GREEN")
    amber_cnt = sum(1 for c in checks if c["status"] == "AMBER")
    red_cnt = sum(1 for c in checks if c["status"] == "RED")
    review_cnt = sum(1 for c in checks if c["status"] == "REVIEW")

    total_score = (
        round(sum(c["score"] for c in verified_checks if c["score"] is not None) / len(verified_checks), 1)
        if verified_checks
        else 100.0
    )

    if red_cnt > 0:
        verdict = f"🔴 객관적 위험 {red_cnt}건 발견 (우선 조치 권장)"
    elif amber_cnt > 0:
        verdict = f"🟡 검증 항목 양호 · 선택 권고 {amber_cnt}건 / 사용자 확인 {review_cnt}건"
    elif review_cnt > 0:
        verdict = f"🔵 JSON 검증 항목 모두 통과 · 아키텍처/코드 {review_cnt}건 확인 필요"
    else:
        verdict = "🟢 Production Ready (모든 항목 검증 완료)"

    return {
        "project_id": project_id,
        "region": region,
        "service_name": disp_name,
        "resource_id": engine_id,
        "runtime_type": "agent_runtime",
        "runtime_label": "Agent Runtime",
        "service_url": eng.get("name", ""),
        "created_by": spec.get("agentFramework", "agent-platform"),
        "service_account": sa_email,
        "inspected_at": now_str,
        "total_score": total_score,
        "verdict": verdict,
        "green_count": green_cnt,
        "amber_count": amber_cnt,
        "red_count": red_cnt,
        "review_count": review_cnt,
        "verified_count": len(verified_checks),
        "detected_config": {
            "min_instances": min_inst if min_inst is not None else 1,
            "max_instances": max_inst if max_inst is not None else 10,
            "location": loc_val or "global",
        },
        "combined_runtime_fix_cmd": (
            f"# ⚠️ 선택한 항목에 따라 UI에서 맞춤형 명령어가 동적으로 생성됩니다.\n"
            f"uvx google-agents-cli deploy --project={project_id} --region={region}"
        ),
        "stage1_skill_cmd": "uv run prod-ready scan <내_로컬_에이전트_코드_폴더>",
        "checks": checks,
    }
