"""Stage 2 (Post-Deployment): Read-Only GCP Live Runtime Inspector & Remediation Guide Generator.

Queries live Cloud Run services, Agent Runtime (Reasoning Engines), and GKE agent workloads
in 100% READ-ONLY mode. Never mutates live resources; instead generates tailored,
copy-paste-ready remediation guides (`gcloud` / `kubectl` CLI, `Terraform`, and Stage 1 `Agent Skill` prompts).
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


# ---------------------------------------------------------------------------
# Checklist v3 classification (Single Source of Truth: prod_ready/catalog_v3.json)
# ---------------------------------------------------------------------------
# The live inspector re-classifies its runtime checks onto checklist v3 item IDs.
# Only agent-characteristic items are scored. General platform best practices
# (scaling, sizing, probes, private networking, PDB/HPA, PSS/Non-Root ...) are
# delegated to platform skills and surfaced as an informational, unscored group.
# Gemini call retry · endpoint (retry_options / GOOGLE_CLOUD_LOCATION) is ADK agent-code territory:
# it is out of scope (ADK optimization) and is not inspected or scored here.

PLATFORM_SKILLS_URL = "https://github.com/google/skills/tree/main/skills/cloud"
PLATFORM_GROUP_LABEL = "플랫폼 Skill 참조 권장"

_CATALOG_REL = Path("skills") / "agent-production-readiness" / "scripts" / "prod_ready" / "catalog_v3.json"

_FALLBACK_CATEGORIES = [
    {"code": "F", "name": "Features", "sub": "에이전트 기능"},
    {"code": "O", "name": "Optimize", "sub": "런타임 최적화"},
    {"code": "D", "name": "Delivery & Eval", "sub": "배포 · 품질 평가"},
    {"code": "OB", "name": "Observability", "sub": "관측성"},
    {"code": "S", "name": "Security", "sub": "보안"},
]
_FALLBACK_CONDITIONS = {
    "conv": "멀티턴 대화",
    "memory": "장기 기억 필요",
    "code": "AI 생성 코드 실행",
    "tools": "MCP·외부 도구 호출",
}


def _load_catalog_v3() -> dict[str, Any]:
    candidates = [Path(__file__).resolve().parent.parent / _CATALOG_REL]
    try:  # installed package layout (pip install .)
        import prod_ready  # type: ignore

        candidates.append(Path(prod_ready.__file__).resolve().parent / "catalog_v3.json")
    except Exception:
        pass
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


_CATALOG_V3 = _load_catalog_v3()
_CATEGORIES = {c["code"]: c for c in (_CATALOG_V3.get("categories") or _FALLBACK_CATEGORIES)}
_CONDITIONS = _CATALOG_V3.get("conditions") or _FALLBACK_CONDITIONS
_ITEMS_BY_ID: dict[str, dict[str, Any]] = {
    item["id"]: item
    for items in (_CATALOG_V3.get("items") or {}).values()
    for item in items
}
_PLATFORM_SKILLS = _CATALOG_V3.get("platform_skills") or {}


def get_checklist_v3_catalog() -> dict[str, Any]:
    """Returns the checklist v3 catalog (read-only copy) for the workbench UI."""
    return {
        "version": _CATALOG_V3.get("version", "v3"),
        "categories": list(_CATEGORIES.values()),
        "conditions": dict(_CONDITIONS),
        "items": _CATALOG_V3.get("items") or {},
        "delegated_platform_bps": _CATALOG_V3.get("delegated_platform_bps") or [],
        "platform_skills": _PLATFORM_SKILLS,
        "platform_skills_repo": _CATALOG_V3.get("platform_skills_repo") or PLATFORM_SKILLS_URL,
    }

# runtime_type -> {legacy runtime check id -> v3 item id}
_V3_MAP: dict[str, dict[str, str]] = {
    "cloud_run": {
        "R2.1": "CR-F1",
        "R3.1": "CR-S1",
        "R4.1": "CR-S2",
        "R4.2": "CR-S3",
        "R4.3": "CR-S4",
        "R5.1": "CR-OB1",
    },
    "agent_runtime": {
        "R2.1": "AR-F1",
        "R4.1": "AR-S1",
        "R4.2": "AR-S2",
        "R4.3": "AR-S3",
        "R5.1": "AR-OB1",
    },
    "gke": {
        "R1.3": "GKE-O1",
        "R2.1": "GKE-F1",
        "R4.1": "GKE-S1",
        "R4.2": "GKE-S3",
        "R4.3": "GKE-S4",
        "R4.4": "GKE-S2",
        "R7.1": "GKE-O2",
        "R5.1": "GKE-OB1",
    },
}

# runtime_type -> {legacy runtime check id -> (platform id, recommended platform skills)}
_PLATFORM_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "cloud_run": {
        "R1.1": ("PLAT-SCALING", "cloud-run-basics"),
        "R1.2": ("PLAT-SIZING", "cloud-run-basics"),
        "R1.3": ("PLAT-PROBE", "cloud-run-basics"),
        "R3.2": ("PLAT-VPC", "cloud-run-basics · google-cloud-waf-security"),
    },
    "agent_runtime": {
        "R3.2": ("PLAT-PSCI", "agent-platform-deploy · google-cloud-waf-security"),
    },
    "gke": {
        "R1.1": ("PLAT-HA", "gke-reliability · gke-productionize"),
        "R1.2": ("PLAT-SIZING", "gke-productionize"),
        "R3.1": ("PLAT-NETPOL", "gke-networking · gke-workload-security"),
        "R4.1P": ("PLAT-PSS", "gke-workload-security"),
    },
}


def _category_fields(code: str) -> dict[str, Any]:
    cat = _CATEGORIES.get(code) or {"code": code, "name": code, "sub": ""}
    return {
        "category": code,
        "category_name": cat.get("name", code),
        "category_sub": cat.get("sub", ""),
        "domain": f"{cat.get('name', code)} · {cat.get('sub', '')}".strip(" ·"),
    }


def _apply_checklist_v3(result: dict[str, Any]) -> dict[str, Any]:
    """Re-classifies legacy runtime checks onto checklist v3 IDs and recomputes summary counts."""
    runtime_type = result.get("runtime_type", "cloud_run")
    v3_map = _V3_MAP.get(runtime_type, {})
    plat_map = _PLATFORM_MAP.get(runtime_type, {})
    scored: list[dict[str, Any]] = []
    platform: list[dict[str, Any]] = []

    for chk in result.get("checks", []):
        legacy = chk.get("id", "")
        if legacy in v3_map:
            v3_id = v3_map[legacy]
            item = _ITEMS_BY_ID.get(v3_id, {})
            code = item.get("category") or v3_id.split("-")[1].rstrip("0123456789")
            cond = item.get("condition")
            chk.update(
                {
                    "id": v3_id,
                    "legacy_id": legacy,
                    **_category_fields(code),
                    "live_title": chk.get("title", ""),
                    "title": item.get("title") or chk.get("title", ""),
                    "preview": bool(item.get("preview", False)),
                    "condition": cond,
                    "condition_label": _CONDITIONS.get(cond) if cond else None,
                    "scored": True,
                    "group": "checklist",
                }
            )
            scored.append(chk)
        elif legacy in plat_map:
            plat_id, skills = plat_map[legacy]
            chk.update(
                {
                    "id": plat_id,
                    "legacy_id": legacy.rstrip("P"),
                    "category": "PLATFORM",
                    "category_name": PLATFORM_GROUP_LABEL,
                    "category_sub": "체크리스트 범위 밖 · 점수 미반영",
                    "domain": PLATFORM_GROUP_LABEL,
                    "observed_status": chk.get("status"),
                    "preview": False,
                    "condition": None,
                    "condition_label": None,
                    "scored": False,
                    "group": "platform_skill",
                    "skills": skills,
                    "skills_url": PLATFORM_SKILLS_URL,
                }
            )
            platform.append(chk)
        else:  # unknown -> keep visible but unscored
            chk.setdefault("scored", False)
            chk.setdefault("group", "platform_skill")
            platform.append(chk)

    order = {item_id: idx for idx, item_id in enumerate(_ITEMS_BY_ID)}
    scored.sort(key=lambda c: order.get(c["id"], 999))

    green_cnt = sum(1 for c in scored if c["status"] == "GREEN")
    amber_cnt = sum(1 for c in scored if c["status"] == "AMBER")
    red_cnt = sum(1 for c in scored if c["status"] == "RED")
    review_cnt = sum(1 for c in scored if c["status"] == "REVIEW")
    # Preview items are inspected and guided, but never block the Production Ready verdict.
    blocking = [c for c in scored if not c.get("preview")]
    red_block = sum(1 for c in blocking if c["status"] == "RED")
    amber_block = sum(1 for c in blocking if c["status"] == "AMBER")
    preview_open = sum(1 for c in scored if c.get("preview") and c["status"] in ("RED", "AMBER", "REVIEW"))
    preview_note = f" · Preview 항목 {preview_open}건은 판정에 미반영" if preview_open else ""
    if red_block > 0:
        verdict = f"🔴 객관적 위험 {red_block}건 발견 (우선 조치 권장){preview_note}"
    elif amber_block > 0:
        verdict = f"🟡 검증 항목 양호 · 선택 권고 {amber_block}건 / 사용자 확인 {review_cnt}건{preview_note}"
    elif review_cnt > 0:
        verdict = f"🔵 JSON 검증 항목 모두 통과 · 아키텍처/코드 {review_cnt}건 확인 필요{preview_note}"
    else:
        verdict = f"🟢 Production Ready (체크리스트 v3 항목 검증 완료){preview_note}"

    result.update(
        {
            "checklist_version": "v3",
            "checks": scored,
            "platform_skill_checks": platform,
            "platform_skills": list(_PLATFORM_SKILLS.get(runtime_type, [])),
            "platform_skills_url": PLATFORM_SKILLS_URL,
            "platform_skill_note": (
                "체크리스트는 에이전트 특성 항목만 다룹니다. 스케일링·사이징·헬스 프로브·사설 네트워크·"
                "PDB·Pod 보안 같은 일반 플랫폼 Best Practice는 google/skills의 플랫폼 Skill을 함께 참고하세요."
            ),
            "verdict": verdict,
            "total_count": len(scored),
            "green_count": green_cnt,
            "amber_count": amber_cnt,
            "red_count": red_cnt,
            "review_count": review_cnt,
            "verified_count": green_cnt + amber_cnt + red_cnt,
        }
    )
    return result


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
    elif runtime_type == "gke":
        bundle = get_sample_runtime_json("gke")
        return _evaluate_gke_workload(project_id, region, service_name or "gke-enterprise-agent", bundle, now_str)
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


_K8S_GKE_KINDS = {
    "Deployment",
    "Namespace",
    "NetworkPolicy",
    "FQDNNetworkPolicy",
    "PodDisruptionBudget",
    "HorizontalPodAutoscaler",
    "PodMonitoring",
    "SandboxTemplate",
    "SandboxWarmPool",
    "SandboxClaim",
    "PodSnapshotPolicy",
    "PodSnapshotStorageConfig",
    "WorkerPool",
    "ActorTemplate",
    "Gateway",
    "HTTPRoute",
    "GCPAgentGateway",
    "GCPBackend",
    "GCPEgressPolicy",
    "GCPAuthzPolicy",
    "GCPExtensionBinding",
    "InferencePool",
    "InferenceObjective",
    "ComputeClass",
    "ServiceAccount",
}


def _is_gke_cluster_dict(obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict):
        return False
    if any(k in obj for k in ("autopilot", "nodePools", "workloadIdentityConfig", "privateClusterConfig", "ipAllocationPolicy", "datapathProvider")):
        return True
    self_link = str(obj.get("selfLink", ""))
    return "container.googleapis.com" in self_link and "/clusters/" in self_link


def _is_k8s_gke_resource(obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict):
        return False
    api_ver = str(obj.get("apiVersion", ""))
    kind = str(obj.get("kind", ""))
    if "serving.knative.dev" in api_ver:
        return False
    if kind in _K8S_GKE_KINDS:
        return True
    if api_ver.startswith((
        "apps/",
        "networking.k8s.io/",
        "networking.gke.io/",
        "policy/",
        "autoscaling/",
        "extensions.agents.x-k8s.io/",
        "podsnapshot.gke.io/",
        "ate.dev/",
        "monitoring.googleapis.com/",
        "gateway.networking.k8s.io/",
        "inference.networking.k8s.io/",
    )):
        return True
    return False


def _is_k8s_list_dict(obj: Any) -> bool:
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        return False
    items = obj["items"]
    if obj.get("kind") == "List":
        return not items or any(_is_k8s_gke_resource(i) or _is_gke_cluster_dict(i) for i in items if isinstance(i, dict))
    return any(_is_k8s_gke_resource(i) for i in items if isinstance(i, dict))


def _parse_json_stream(cleaned: str) -> list[Any]:
    """Parses either a single JSON document or multiple concatenated JSON objects
    (e.g., sequential `kubectl get ... -o json` and `gcloud container clusters describe --format=json` outputs).
    """
    try:
        return [json.loads(cleaned, strict=False)]
    except json.JSONDecodeError as first_err:
        decoder = json.JSONDecoder(strict=False)
        docs: list[Any] = []
        idx = 0
        n = len(cleaned)
        while idx < n:
            while idx < n and cleaned[idx].isspace():
                idx += 1
            if idx >= n:
                break
            try:
                obj, end = decoder.raw_decode(cleaned, idx)
                docs.append(obj)
                idx = end
            except json.JSONDecodeError:
                if not docs:
                    raise first_err
                # Skip stray terminal prompt/warning text between JSON outputs
                next_obj = cleaned.find("{", idx + 1)
                next_arr = cleaned.find("[", idx + 1)
                candidates = [p for p in (next_obj, next_arr) if p != -1]
                if not candidates:
                    break
                idx = min(candidates)
        if not docs:
            raise first_err
        return docs


def _flatten_k8s_items(raw_items: list[Any]) -> list[dict[str, Any]]:
    """Flattens nested Kubernetes List objects, arrays, and GkeAgentBundles into individual resource dicts."""
    flat: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, list):
            flat.extend(_flatten_k8s_items(item))
        elif isinstance(item, dict):
            if _is_k8s_list_dict(item):
                flat.extend(_flatten_k8s_items(item.get("items", [])))
            elif item.get("kind") == "GkeAgentBundle":
                for single_key in ("cluster", "namespace", "deployment", "pdb", "hpa", "podMonitoring", "sandbox", "substrate"):
                    if isinstance(item.get(single_key), dict):
                        flat.append(item[single_key])
                for list_key in ("networkPolicies", "agentGateways"):
                    if isinstance(item.get(list_key), list):
                        flat.extend(_flatten_k8s_items(item[list_key]))
            else:
                flat.append(item)
    return flat


def _extract_gke_meta(bundle: dict[str, Any], project_hint: str, region_hint: str) -> tuple[str, str, str]:
    cluster = bundle.get("cluster") or {}
    deployment = bundle.get("deployment") or {}
    sandbox = bundle.get("sandbox") or {}
    substrate = bundle.get("substrate") or {}
    primary = deployment or sandbox or substrate
    meta = primary.get("metadata", {}) if isinstance(primary, dict) else {}

    project_id = project_hint or "my-gcp-project"
    region = str(cluster.get("location") or region_hint or "us-central1")
    workload_name = str(meta.get("name") or cluster.get("name") or "gke-agent")

    # Try extracting GOOGLE_CLOUD_PROJECT from deployment container env or cluster workloadPool
    containers = (
        deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        if isinstance(deployment, dict)
        else []
    )
    if containers and isinstance(containers[0], dict):
        for e in containers[0].get("env", []):
            if e.get("name") == "GOOGLE_CLOUD_PROJECT" and e.get("value"):
                project_id = str(e.get("value"))
                break
    if project_id == "my-gcp-project":
        wp = str(cluster.get("workloadIdentityConfig", {}).get("workloadPool", ""))
        if wp.endswith(".svc.id.goog"):
            project_id = wp.removesuffix(".svc.id.goog")
        else:
            self_link = str(cluster.get("selfLink", ""))
            if "/projects/" in self_link:
                parts = self_link.split("/projects/")[1].split("/")
                if parts and parts[0]:
                    project_id = parts[0]

    return project_id, region, workload_name


def _group_k8s_items_into_bundles(items: list[Any]) -> list[dict[str, Any]]:
    flat_items = _flatten_k8s_items(items)
    deployments: list[dict[str, Any]] = []
    namespaces: dict[str, dict[str, Any]] = {}
    pdbs: list[dict[str, Any]] = []
    hpas: list[dict[str, Any]] = []
    netpols: list[dict[str, Any]] = []
    pod_monitorings: list[dict[str, Any]] = []
    sandboxes: list[dict[str, Any]] = []
    substrates: list[dict[str, Any]] = []
    gateways: list[dict[str, Any]] = []
    cluster: dict[str, Any] = {}

    for item in flat_items:
        if not isinstance(item, dict):
            continue
        if _is_gke_cluster_dict(item):
            cluster = item
            continue
        kind = str(item.get("kind", ""))
        name = str(item.get("metadata", {}).get("name", ""))
        if kind == "Deployment":
            deployments.append(item)
        elif kind == "Namespace" and name:
            namespaces[name] = item
        elif kind == "PodDisruptionBudget":
            pdbs.append(item)
        elif kind == "HorizontalPodAutoscaler":
            hpas.append(item)
        elif kind in ("NetworkPolicy", "FQDNNetworkPolicy"):
            netpols.append(item)
        elif kind == "PodMonitoring":
            pod_monitorings.append(item)
        elif kind in ("SandboxTemplate", "SandboxWarmPool", "SandboxClaim", "PodSnapshotPolicy", "PodSnapshotStorageConfig"):
            sandboxes.append(item)
        elif kind in ("WorkerPool", "ActorTemplate"):
            substrates.append(item)
        elif kind in ("Gateway", "HTTPRoute", "GCPAgentGateway", "GCPBackend", "GCPEgressPolicy", "GCPAuthzPolicy", "GCPExtensionBinding"):
            gateways.append(item)

    if not any([deployments, namespaces, pdbs, hpas, netpols, pod_monitorings, sandboxes, substrates, gateways, cluster]):
        return []

    if not deployments:
        ns_obj = next(iter(namespaces.values()), None)
        return [
            {
                "kind": "GkeAgentBundle",
                "cluster": cluster or None,
                "namespace": ns_obj,
                "deployment": None,
                "pdb": pdbs[0] if pdbs else None,
                "hpa": hpas[0] if hpas else None,
                "networkPolicies": netpols,
                "podMonitoring": pod_monitorings[0] if pod_monitorings else None,
                "sandbox": sandboxes[0] if sandboxes else None,
                "substrate": substrates[0] if substrates else None,
                "agentGateways": gateways,
            }
        ]

    bundles: list[dict[str, Any]] = []
    for dep in deployments:
        dep_ns = str(dep.get("metadata", {}).get("namespace", "default"))
        ns_obj = namespaces.get(dep_ns) or (next(iter(namespaces.values()), None) if len(namespaces) == 1 else None)
        dep_pdbs = [p for p in pdbs if str(p.get("metadata", {}).get("namespace", dep_ns)) == dep_ns]
        dep_hpas = [h for h in hpas if str(h.get("metadata", {}).get("namespace", dep_ns)) == dep_ns]
        dep_nps = [n for n in netpols if str(n.get("metadata", {}).get("namespace", dep_ns)) == dep_ns]
        dep_pms = [m for m in pod_monitorings if str(m.get("metadata", {}).get("namespace", dep_ns)) == dep_ns]
        bundles.append(
            {
                "kind": "GkeAgentBundle",
                "cluster": cluster or None,
                "namespace": ns_obj,
                "deployment": dep,
                "pdb": dep_pdbs[0] if dep_pdbs else None,
                "hpa": dep_hpas[0] if dep_hpas else None,
                "networkPolicies": dep_nps,
                "podMonitoring": dep_pms[0] if dep_pms else None,
                "sandbox": sandboxes[0] if sandboxes else None,
                "substrate": substrates[0] if substrates else None,
                "agentGateways": gateways,
            }
        )
    return bundles


def inspect_pasted_json(
    raw_json: str,
    project_id_hint: str = "",
    region_hint: str = "us-central1",
) -> dict[str, Any]:
    """Parses user-pasted CLI JSON output (Cloud Run, Agent Runtime, or GKE kubectl/gcloud)
    including multi-JSON terminal streams without requiring any GCP IAM permissions on the server.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cleaned = _sanitize_terminal_json(raw_json)
    if not cleaned:
        raise ValueError("붙여넣은 JSON 내용이 비어 있습니다. 터미널 명령어 실행 결과를 붙여넣어 주세요.")

    docs = _parse_json_stream(cleaned)
    results: list[dict[str, Any]] = []

    # Multi-JSON stream (e.g., `kubectl get deploy... -o json; kubectl get ns... -o json; gcloud container clusters describe... --format=json`)
    if len(docs) > 1:
        has_any_gke = any(
            (isinstance(d, dict) and (_is_k8s_gke_resource(d) or _is_k8s_list_dict(d) or _is_gke_cluster_dict(d) or d.get("kind") == "GkeAgentBundle"))
            or (isinstance(d, list) and any(isinstance(x, dict) and (_is_k8s_gke_resource(x) or _is_k8s_list_dict(x) or _is_gke_cluster_dict(x)) for x in d))
            for d in docs
        )
        if has_any_gke:
            for bundle in _group_k8s_items_into_bundles(docs):
                p_id, reg, w_name = _extract_gke_meta(bundle, project_id_hint, region_hint)
                results.append(_evaluate_gke_workload(p_id, reg, w_name, bundle, now_str))
            if not results:
                raise ValueError("유효한 Cloud Run, Agent Runtime(ReasoningEngine), 또는 GKE(kubectl / gcloud container) JSON 형식을 찾지 못했습니다.")
            return {"count": len(results), "results": results}

    for parsed in docs:
        # Case 1: Array of resources (`gcloud run services list`, `gcloud container clusters list`, or K8s array / `[kubectl_list, cluster]`)
        if isinstance(parsed, list):
            if any(_is_k8s_gke_resource(item) or _is_k8s_list_dict(item) for item in parsed if isinstance(item, dict)):
                for bundle in _group_k8s_items_into_bundles(parsed):
                    p_id, reg, w_name = _extract_gke_meta(bundle, project_id_hint, region_hint)
                    results.append(_evaluate_gke_workload(p_id, reg, w_name, bundle, now_str))
            else:
                for item in parsed:
                    if isinstance(item, dict) and _is_gke_cluster_dict(item):
                        bundle = {"kind": "GkeAgentBundle", "cluster": item}
                        p_id, reg, w_name = _extract_gke_meta(bundle, project_id_hint, region_hint)
                        results.append(_evaluate_gke_workload(p_id, reg, w_name, bundle, now_str))
                    elif isinstance(item, dict) and ("spec" in item or "metadata" in item):
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

            # Case 3: GKE Agent Bundle JSON (1-click sample or combined cluster + K8s manifests)
            elif parsed.get("kind") == "GkeAgentBundle" or (
                any(k in parsed for k in ("deployment", "cluster", "sandbox", "substrate"))
                and "reasoningEngines" not in parsed
                and not str(parsed.get("apiVersion", "")).startswith("serving.knative.dev")
            ):
                p_id, reg, w_name = _extract_gke_meta(parsed, project_id_hint, region_hint)
                results.append(_evaluate_gke_workload(p_id, reg, w_name, parsed, now_str))

            # Case 4: `{"items": [...]}` — distinguish Kubernetes List (`kubectl get ... -o json`) vs Cloud Run v1 List
            elif "items" in parsed and isinstance(parsed["items"], list):
                items = parsed["items"]
                if _is_k8s_list_dict(parsed):
                    for bundle in _group_k8s_items_into_bundles(items):
                        p_id, reg, w_name = _extract_gke_meta(bundle, project_id_hint, region_hint)
                        results.append(_evaluate_gke_workload(p_id, reg, w_name, bundle, now_str))
                else:
                    for item in items:
                        p_id, reg, s_name = _extract_cloud_run_meta(item, project_id_hint, region_hint)
                        results.append(_evaluate_cloud_run_service(p_id, reg, s_name, item, now_str))

            # Case 5: Single Kubernetes Resource (`kubectl get deployment ... -o json`, SandboxTemplate, WorkerPool, etc.)
            elif _is_k8s_gke_resource(parsed):
                for bundle in _group_k8s_items_into_bundles([parsed]):
                    p_id, reg, w_name = _extract_gke_meta(bundle, project_id_hint, region_hint)
                    results.append(_evaluate_gke_workload(p_id, reg, w_name, bundle, now_str))

            # Case 6: Single GKE Cluster JSON (`gcloud container clusters describe ... --format=json`)
            elif _is_gke_cluster_dict(parsed):
                bundle = {"kind": "GkeAgentBundle", "cluster": parsed}
                p_id, reg, w_name = _extract_gke_meta(bundle, project_id_hint, region_hint)
                results.append(_evaluate_gke_workload(p_id, reg, w_name, bundle, now_str))

            # Case 7: Single Agent Runtime ReasoningEngine JSON
            elif "reasoningEngines/" in str(parsed.get("name", "")) or "deploymentSpec" in parsed.get("spec", {}) or "packageSpec" in parsed.get("spec", {}):
                p_id, reg, e_id = _extract_reasoning_engine_meta(parsed, project_id_hint, region_hint)
                results.append(_evaluate_agent_runtime_engine(p_id, reg, e_id, parsed, now_str))

            # Case 8: Single Cloud Run Service JSON (`gcloud run services describe <NAME> --format=json`)
            else:
                p_id, reg, s_name = _extract_cloud_run_meta(parsed, project_id_hint, region_hint)
                results.append(_evaluate_cloud_run_service(p_id, reg, s_name, parsed, now_str))

    if not results:
        raise ValueError("유효한 Cloud Run, Agent Runtime(ReasoningEngine), 또는 GKE(kubectl / gcloud container) JSON 형식을 찾지 못했습니다.")

    return {
        "count": len(results),
        "results": results,
    }


def get_sample_runtime_json(runtime_type: str = "cloud_run") -> dict[str, Any]:
    """Returns a realistic sample JSON payload so users can test the paste-and-analyze workflow with 1 click."""
    if runtime_type == "gke":
        return {
            "kind": "GkeAgentBundle",
            "cluster": {
                "name": "gke-enterprise-agent",
                "location": "us-central1",
                "currentMasterVersion": "1.37.2-gke.1042000",
                "autopilot": {"enabled": True},
                "networkConfig": {
                    "datapathProvider": "ADVANCED_DATAPATH",
                    "defaultEnablePrivateNodes": True,
                },
                "workloadIdentityConfig": {
                    "workloadPool": "geap-sandbox-kwlee.svc.id.goog",
                },
            },
            "namespace": {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": "gke-enterprise-agent",
                    "labels": {
                        "kubernetes.io/metadata.name": "gke-enterprise-agent",
                    },
                },
            },
            "deployment": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "gke-enterprise-agent",
                    "namespace": "gke-enterprise-agent",
                    "labels": {
                        "app": "gke-enterprise-agent",
                        "created-by": "agents-cli",
                    },
                },
                "spec": {
                    "replicas": 1,
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "gke-enterprise-agent",
                            },
                        },
                        "spec": {
                            "serviceAccountName": "gke-enterprise-agent-ksa",
                            "containers": [
                                {
                                    "name": "gke-enterprise-agent",
                                    "image": "us-central1-docker.pkg.dev/geap-sandbox-kwlee/agent-repo/gke-enterprise-agent:latest",
                                    "ports": [{"containerPort": 8080}],
                                    "resources": {
                                        "requests": {"cpu": "1", "memory": "4Gi"},
                                        "limits": {"cpu": "1", "memory": "4Gi"},
                                    },
                                    "env": [
                                        {"name": "GOOGLE_GENAI_USE_ENTERPRISE", "value": "true"},
                                        {"name": "GOOGLE_CLOUD_PROJECT", "value": "geap-sandbox-kwlee"},
                                        {"name": "GOOGLE_CLOUD_LOCATION", "value": "global"},
                                        {"name": "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "value": "true"},
                                        {"name": "MCP_SERVER_URL", "value": "https://currency-mcp-server-r6j5nyqrla-uc.a.run.app/mcp"},
                                    ],
                                    "startupProbe": {"tcpSocket": {"port": 8080}},
                                    "readinessProbe": {"tcpSocket": {"port": 8080}},
                                }
                            ],
                        },
                    },
                },
            },
            "pdb": None,
            "hpa": None,
            "networkPolicies": [],
            "podMonitoring": None,
            "sandbox": None,
            "substrate": None,
            "agentGateways": [],
        }
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


def _evaluate_cloud_run_service_legacy(
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
    functional_type = str(
        meta_ann.get("run.googleapis.com/functional-type")
        or svc.get("functionalType")
        or ""
    ).strip()
    identity_type = str(
        tmpl_ann.get("run.googleapis.com/identity-type")
        or (tmpl_spec.get("workloadIdentityConfig") or {}).get("identityType")
        or (tmpl.get("workloadIdentityConfig") or {}).get("identityType")
        or ""
    ).strip()
    is_mcp_server = functional_type.lower() in (
        "mcp-server",
        "functional_type_mcp_server",
    )
    has_functional_agent = functional_type.lower() in (
        "agent",
        "functional_type_agent",
    )
    has_agent_identity = identity_type.lower() in (
        "agent-identity",
        "identity_type_agent_identity",
        "agent_identity",
    )
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

    remote_url_keys = ("MCP_SERVER_URL", "MCP_URL", "A2A_AGENT_URL", "A2A_SERVER_URL", "AGENT_CARD_URL")
    hardcoded_remote_urls = [
        f"{k}={v}"
        for k, v in env_map.items()
        if (
            k.upper() in remote_url_keys
            or k.upper().endswith(("_MCP_URL", "_MCP_SERVER_URL", "_A2A_URL", "_AGENT_CARD_URL"))
        )
        and v.strip().lower().startswith(("http://", "https://"))
    ]

    startup_probe = container.get("startupProbe", {})
    liveness_probe = container.get("livenessProbe", {})
    has_http_liveness = "httpGet" in liveness_probe
    has_http_startup = "httpGet" in startup_probe
    port_num = container.get("ports", [{"containerPort": 8080}])[0].get("containerPort", 8080)

    checks: list[dict[str, Any]] = []

    # 1. [R1.1] Scaling & Cold Start / CPU Allocation (Workload / SLA Trade-off)
    if min_scale >= 1 and cpu_boost and not cpu_throttling:
        r11_status = "GREEN"
        r11_current = f"minScale={min_scale}, maxScale={max_scale}, startup-cpu-boost=true, cpu-throttling=false (항시 Warm)"
    else:
        r11_status = "REVIEW"
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
        "current_state": r11_current,
        "recommended_state": "트래픽 패턴·지연 SLA에 따라 결정 (간헐 트래픽: min=0 + cpu-boost / 상시 SLA·비동기 Memory Bank·OTel flush: min>=1 + --no-cpu-throttling)",
        "why_it_matters": (
            "minScale=0 + startup-cpu-boost=true 조합은 트래픽이 간헐적이거나 유휴 비용을 줄여야 하는 환경에서 합리적인 설정입니다. "
            "반면 Cloud Run 기본값(--cpu-throttling / cpu_idle=true)은 HTTP 응답 반환 직후 컨테이너 CPU를 즉시 스로틀링하므로, "
            "ADK의 비동기 after_agent_callback(Memory Bank 저장)이나 OpenTelemetry 배치 스팬이 유실·동결될 수 있습니다. "
            "비동기 후처리 또는 엄격한 첫 응답 SLA가 있다면 min-instances >= 1 및 --no-cpu-throttling(cpu_idle=false)을 적용하세요."
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
    r12_status = "REVIEW"
    if is_default_sizing:
        r12_current = f"CPU={cpu_str}, Memory={mem_str}, Concurrency={concurrency} (Cloud Run 기본 사이징)"
    else:
        r12_current = f"CPU={cpu_str}, Memory={mem_str}, Concurrency={concurrency} (사용자 커스텀 설정됨)"

    checks.append({
        "id": "R1.2",
        "domain": "1. 런타임 & 스케일링",
        "check_type": "WORKLOAD_TUNING",
        "verification_scope": "WORKLOAD_TUNING",
        "title": "CPU · 메모리 · 동시성(Concurrency) 사이징 (부하 테스트 기반 확인)",
        "status": r12_status,
        "current_state": r12_current,
        "recommended_state": "부하 테스트(Locust) 기반 CPU/Memory/Concurrency 확정 (자동 상향 금지)",
        "why_it_matters": (
            f"JSON만으로는 {service_name}의 실제 피크 메모리·CPU 사용량을 알 수 없으므로 부하 테스트(`tests/load_test/`) 기반으로 적정 스펙을 확정해야 합니다. "
            "동시성(`concurrency`)을 높일 때는 요청당 컨텍스트 메모리에 비례해 메모리(`memory`)를 함께 조정하세요."
        ),
        "how_to_fix_title": "부하 테스트 기반 스펙 반영",
        "gcloud_cmd": (
            f"# ⚠️ 아래 CPU/Memory/Concurrency는 부하 테스트 결과에 맞춰 직접 수정 후 실행하세요\n"
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
        "console_guide": f"Cloud Monitoring에서 {service_name}의 메모리/CPU 사용률과 응답 시간을 확인한 뒤, 부하 테스트 결과에 맞춰 스펙을 설정하세요.",
    })

    # 3. [R1.3] Health Probes & Execution Environment
    if has_http_liveness:
        r13_status = "GREEN"
        liveness_path = liveness_probe.get("httpGet", {}).get("path", "/")
        r13_current = f"HTTP Liveness Probe 설정됨 ({liveness_path}:{port_num}), 실행 환경: {exec_env}"
    else:
        r13_status = "REVIEW"
        probe_desc = "HTTP Startup Probe" if has_http_startup else f"TCP Startup Probe (port {port_num})"
        r13_current = f"{probe_desc}만 사용 중 (HTTP Liveness Probe 미설정, 실행 환경: {exec_env})"

    checks.append({
        "id": "R1.3",
        "domain": "1. 런타임 & 스케일링",
        "check_type": "WORKLOAD_TUNING",
        "verification_scope": "WORKLOAD_TUNING",
        "title": "HTTP Liveness 헬스 프로브 & 실행 환경(Gen2) 확인",
        "status": r13_status,
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
        env_map.get("AGENT_ENGINE_SESSION_ID")
        or env_map.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
        or env_map.get("AGENT_ENGINE_ID")
        or env_map.get("REASONING_ENGINE_ID")
        or ""
    )
    has_vertex_session = "agentengine://" in session_uri.lower() or bool(agent_engine_ref)
    has_db = bool(cloudsql_instances or env_map.get("INSTANCE_CONNECTION_NAME") or (session_uri and not has_vertex_session))
    has_redis = bool(env_map.get("REDIS_HOST"))

    if has_vertex_session:
        r21_status = "GREEN"
        r21_current = f"Agent Platform Managed Session Service / Memory Bank 연동 감지됨 ({session_uri or agent_engine_ref})"
    elif has_db and has_redis:
        r21_status = "GREEN"
        r21_current = f"Self-Hosted Cloud SQL ({cloudsql_instances or env_map.get('INSTANCE_CONNECTION_NAME') or session_uri}) + Redis ({env_map.get('REDIS_HOST')}) 연동됨"
    elif has_db:
        r21_status = "GREEN"
        r21_current = f"Self-Hosted 외부 세션 DB 연동 감지됨 ({cloudsql_instances or env_map.get('INSTANCE_CONNECTION_NAME') or session_uri})"
    else:
        r21_status = "REVIEW"
        r21_current = (
            "Cloud Run JSON에서 Managed Session(AGENT_ENGINE_SESSION_ID / ADK_SESSION_SERVICE_URI) 미감지 "
            "(※ 단, 코드 내부 연동 또는 Stateless 단일턴 여부는 JSON만으로 판별 불가)"
        )

    checks.append({
        "id": "R2.1",
        "domain": "2. 세션 & 상태 영속성",
        "check_type": "ARCH_DECISION",
        "verification_scope": "ARCH_DECISION",
        "title": "멀티턴 대화 영속성 (기본: Agent Platform Managed Sessions & Memory Bank)",
        "status": r21_status,
        "current_state": r21_current,
        "recommended_state": (
            "멀티턴 운영 기본 권장: Agent Platform Managed Session Service & Memory Bank (`AGENT_ENGINE_SESSION_ID` 연동 — Cloud Run OTel 리소스 감지 보존) / "
            "Stateless 단일턴이면 불필요 / (참고 옵션: Self-Hosted Cloud SQL·Redis·Firestore 직접 구축)"
        ),
        "why_it_matters": (
            "`gcloud run services describe` JSON만으로는 컨테이너 Python 코드 내부에서 Agent Platform Managed Session Service·Memory Bank를 호출하는지, "
            "아니면 Stateless 에이전트인지 판별할 수 없습니다. 멀티턴 대화가 필요한데 `InMemorySessionService`만 사용 중이라면 인스턴스 스케일아웃·재시작 시 대화가 유실되므로, "
            "별도 DB 운영 부담이 없는 **Agent Platform Managed Sessions & Memory Bank**(`AGENT_ENGINE_SESSION_ID`) 연동을 기본 권장합니다. "
            "(※ Cloud Run에서는 `GOOGLE_CLOUD_AGENT_ENGINE_ID` 대신 `AGENT_ENGINE_SESSION_ID`를 사용해야 ADK OTel이 Cloud Run 리소스를 정상 감지합니다.)"
        ),
        "how_to_fix_title": "Agent Platform Managed Sessions & Memory Bank 환경변수 연결",
        "gcloud_cmd": (
            f"# [기본 권장 · Managed] Agent Platform Session Service & Memory Bank 연결 (Cloud Run OTel 충돌 방지용 별도 변수명)\n"
            f"gcloud run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --update-env-vars=\"AGENT_ENGINE_SESSION_ID=<AGENT_ENGINE_ID>\"\n\n"
            f"# (참고: Self-Hosted Cloud SQL을 직접 운영할 경우 --add-cloudsql-instances 옵션 사용 가능)"
        ),
        "terraform_snippet": (
            '# 기본 권장: Agent Platform Managed Session & Memory Bank (Cloud Run OTel 리소스 감지 보존)\n'
            'env {\n  name  = "AGENT_ENGINE_SESSION_ID"\n  value = google_vertex_ai_reasoning_engine.memory_engine.name\n}'
        ),
        "console_guide": "멀티턴 운영 시 `AGENT_ENGINE_SESSION_ID`를 연결해 Managed Session & Memory Bank를 사용하세요. (Stateless이거나 이미 Self-Hosted DB를 운영 중이라면 그대로 유지 가능)",
    })

    # 5. [R3.1] Inbound Ingress Exposure & Auth Check (Public vs Internal depends on intent!)
    if ingress in ("internal", "internal-and-cloud-load-balancing"):
        r31_status = "GREEN"
        r31_current = f"ingress = {ingress} (내부 VPC 또는 Cloud Load Balancing 경유 트래픽만 허용)"
    else:
        r31_status = "REVIEW"
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
        r32_status = "GREEN"
        r32_current = f"Direct VPC / 커넥터 연결됨 ({vpc_egress or 'private-ranges-only'})"
    else:
        r32_status = "REVIEW"
        hint = f" (감지된 아웃바운드 URL: {', '.join(public_url_envs[:2])})" if public_url_envs else ""
        r32_current = f"Direct VPC Egress 미연결 — 퍼블릭 API/Gemini/퍼블릭 MCP만 호출한다면 불필요{hint}"

    checks.append({
        "id": "R3.2",
        "domain": "3. 네트워크 & 엣지 보안",
        "check_type": "ARCH_DECISION",
        "verification_scope": "ARCH_DECISION",
        "title": "아웃바운드 Direct VPC Egress 필요 여부 확인 (사설 IP 리소스 호출 시에만 필요)",
        "status": r32_status,
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
    if has_agent_identity:
        r41_status = "GREEN"
        r41_current = f"Agent Identity (SPIFFE ID) 활성화됨 ({identity_type}) — 기본 Compute Engine SA 미사용"
    elif not is_default_compute_sa:
        r41_status = "GREEN"
        r41_current = f"전용 서비스 계정 사용 중 ({sa_email})"
    else:
        r41_status = "RED"
        r41_current = f"기본 Compute Engine 서비스 계정 사용 중 ({sa_email or 'default'}) — 과다 권한 위험 (JSON에서 직접 확인됨)"

    checks.append({
        "id": "R4.1",
        "domain": "4. 보안, 신원 & 거버넌스",
        "check_type": "RUNTIME_VERIFIED",
        "verification_scope": "RUNTIME_VERIFIED",
        "title": "전용 최소 권한 서비스 계정 (Dedicated IAM Service Account)",
        "status": r41_status,
        "current_state": r41_current,
        "recommended_state": "에이전트 전용 서비스 계정 또는 Agent Identity (SPIFFE ID) 사용 + 필요한 최소 IAM Role만 부여",
        "why_it_matters": (
            "Cloud Run `describe` JSON의 `serviceAccountName` / `identity-type` 필드로 객관적 검증이 가능한 항목입니다. "
            "기본 Compute 서비스 계정은 프로젝트 전체에 광범위한 권한이 부여되어 있는 경우가 많으므로, "
            "전용 서비스 계정 또는 Agent Identity로 분리하고 최소 권한만 부여하는 것이 안전합니다."
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
        "console_guide": f"전용 SA 또는 Agent Identity가 연결되어 있더라도 `gcloud projects get-iam-policy {project_id}`로 과도한 역할(Editor/Owner 등)이 없는지 주기적으로 점검하세요.",
    })

    # 8. [R4.2] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (Agent & MCP Server aware)
    if is_mcp_server:
        r42_status = "GREEN"
        r42_current = (
            f"Cloud Run MCP Server 감지: functional-type={functional_type} "
            "(Agent Registry 자동 등록 구성됨 · MCP 서버는 전용 서비스 계정 유지 가능)"
        )
    elif has_functional_agent and has_agent_identity:
        r42_status = "GREEN"
        r42_current = f"functional-type={functional_type}, identity-type={identity_type} (SPIFFE Agent Identity & Agent Registry 자동 등록 구성됨)"
    elif has_functional_agent or has_agent_identity:
        r42_status = "AMBER"
        r42_current = (
            f"일부만 설정됨 (functional-type={functional_type or '미설정'}, identity-type={identity_type or '미설정'}) — "
            "에이전트 서비스는 두 항목을 함께 설정해야 SPIFFE ID 발급과 Agent Registry 자동 등록이 완성됩니다."
        )
    else:
        r42_status = "AMBER"
        r42_current = "functional-type=agent(또는 mcp-server) 및 identity-type=agent-identity 미설정 (전통적 서비스 계정 모드 · Agent Registry 자동 등록 안 됨)"

    r42_lazy_note = ""
    if hardcoded_remote_urls:
        preview_urls = ", ".join(hardcoded_remote_urls[:2])
        r42_current += (
            f" · 💡 원격 MCP/A2A 정적 URL 환경변수 감지({preview_urls}) — "
            "하드코딩 대신 ADK AgentRegistry(get_mcp_toolset / get_remote_a2a_agent) Lazy Discovery 전환 권장"
        )
        r42_lazy_note = (
            " 원격 MCP 서버나 A2A 서브 에이전트를 정적 URL 환경변수로 직접 호출하는 경우, "
            "ADK의 `google.adk.integrations.agent_registry.AgentRegistry` (`get_mcp_toolset()` / `get_remote_a2a_agent()`)로 전환하면 "
            "엔드포인트 자동 해석·GCP 인증·도구 단위 거버넌스를 통합할 수 있습니다."
        )

    r42_verify_list_cmd = (
        f"gcloud agent-registry mcp-servers list --location={region} --project={project_id}"
        if is_mcp_server
        else f"gcloud agent-registry agents list --location={region} --project={project_id}"
    )

    checks.append({
        "id": "R4.2",
        "domain": "4. 보안, 신원 & 거버넌스",
        "check_type": "RUNTIME_RECOMMENDED",
        "verification_scope": "RUNTIME_VERIFIED",
        "title": "Agent Identity (SPIFFE ID) & Agent Registry 자동 등록",
        "status": r42_status,
        "current_state": r42_current,
        "recommended_state": (
            "Cloud Run MCP Server: functional_type=FUNCTIONAL_TYPE_MCP_SERVER (전용 SA 유지 가능)"
            if is_mcp_server
            else "Agent: functional_type=FUNCTIONAL_TYPE_AGENT + identity_type=IDENTITY_TYPE_AGENT_IDENTITY (--no-traffic로 안전 전환) · MCP Server: FUNCTIONAL_TYPE_MCP_SERVER"
        ),
        "why_it_matters": (
            "Cloud Run 에이전트에 `--functional-type=agent`와 `--identity-type=agent-identity`를 설정하면 에이전트별 고유 SPIFFE ID가 발급되고 "
            "Agent Registry에 자동 등록됩니다. Cloud Run MCP 서버의 경우 `--functional-type=mcp-server`만 지정해도 Agent Registry에 자동 등록되며 전용 서비스 계정을 유지할 수 있습니다. "
            "(※ `functional-type`은 최초 지정 후 변경 불가하며, 기존 에이전트에 적용할 때는 `--no-traffic`으로 새 리비전을 먼저 만든 뒤 SPIFFE Principal에 IAM 권한을 부여하고 트래픽을 전환하세요.)"
            + r42_lazy_note
        ),
        "how_to_fix_title": "Agent Identity 활성화 및 Agent Registry 자동 등록 확인",
        "gcloud_cmd": (
            f"# [Step 1] Agent Registry & App Hub API 활성화\n"
            f"gcloud services enable agentregistry.googleapis.com apphub.googleapis.com --project={project_id}\n\n"
            f"# [Step 2] Agent Identity + Functional Type 적용 (※ MCP 서버는 --functional-type=mcp-server 만 지정 가능)\n"
            f"gcloud beta run services update {service_name} \\\n"
            f"  --region={region} \\\n"
            f"  --project={project_id} \\\n"
            f"  --functional-type={'mcp-server' if is_mcp_server else 'agent'} \\\n"
            + ("" if is_mcp_server else "  --identity-type=agent-identity \\\n")
            + f"  --no-traffic\n\n"
            f"# [Step 3 · 읽기 전용 확인] Agent Registry 자동 등록 조회\n"
            f"{r42_verify_list_cmd}"
        ),
        "terraform_snippet": (
            'provider        = google-beta\n'
            'functional_type = "FUNCTIONAL_TYPE_MCP_SERVER" # MCP 서버는 전용 SA 유지 가능'
            if is_mcp_server
            else (
                'provider        = google-beta\n'
                'functional_type = "FUNCTIONAL_TYPE_AGENT"\n'
                'template {\n'
                '  workload_identity_config {\n'
                '    identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"\n'
                '  }\n'
                '}'
            )
        ),
        "console_guide": f"Agent Registry 콘솔 또는 `{r42_verify_list_cmd}`에서 {service_name}가 자동 등록되었는지 확인하세요.",
    })

    # 9. [R4.3 -> CR-S4] Agent Gateway for Cloud Run (Preview) — 조건부: MCP·외부 도구 호출
    has_guardrail_env = env_map.get("MODEL_ARMOR_ENABLED", "").lower() == "true" or "MODEL_ARMOR_TEMPLATE_ID" in env_map
    agw_annotations = [
        f"{k}={v}"
        for k, v in {**meta_ann, **tmpl_ann}.items()
        if "agent-gateway" in k.lower() or "agentgateway" in k.lower()
    ]
    if agw_annotations:
        r43_status = "GREEN"
        r43_current = f"Agent Gateway 연결 감지됨 ({', '.join(agw_annotations[:2])})" + (
            " · Model Armor env 감지됨" if has_guardrail_env else ""
        )
    else:
        r43_status = "REVIEW"
        r43_current = (
            "Agent Gateway 연결 미감지 — MCP·외부 도구를 호출하는 에이전트라면 Agent Gateway for Cloud Run(Preview) 적용 검토"
            + (" · Model Armor env 감지됨" if has_guardrail_env else "")
            + " (외부 도구 호출이 없으면 해당 없음)"
        )

    checks.append({
        "id": "R4.3",
        "domain": "Security",
        "check_type": "ARCH_DECISION",
        "verification_scope": "ARCH_DECISION",
        "title": "Agent Gateway for Cloud Run (외부 호출 통제 · Preview)",
        "status": r43_status,
        "current_state": r43_current,
        "recommended_state": (
            "MCP·외부 도구를 호출하는 에이전트: Agent Gateway for Cloud Run(Preview)으로 외부 호출에 접근 정책 적용 "
            "(초기에는 DRY_RUN으로 로그 검증 후 강제) / 외부 도구 호출이 없으면 해당 없음"
        ),
        "why_it_matters": (
            "에이전트가 무엇을 호출할지는 LLM이 실행 시점에 정하므로, 코드 리뷰만으로는 외부 호출을 통제할 수 없습니다. "
            "Agent Gateway for Cloud Run(Preview)을 거치게 하면 MCP·A2A·외부 API 호출에 중앙 접근 정책과 Model Armor 검사를 "
            "코드 변경 없이 적용할 수 있습니다. `describe` JSON만으로는 게이트웨이 정책 내용까지 확인할 수 없으므로 아래 읽기 전용 명령으로 함께 확인하세요."
        ),
        "how_to_fix_title": "Agent Gateway for Cloud Run 구성 확인 (MCP·외부 도구 호출 시)",
        "gcloud_cmd": (
            f"# [Step 1 · 읽기 전용 확인] 프로젝트의 Agent Gateway 목록 조회\n"
            f"gcloud beta network-services agent-gateways list --location={region} --project={project_id}\n\n"
            f"# [Step 2] 로컬 코드에 CR-S4(Agent Gateway) 구성 생성\n"
            f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target cloud_run --items CR-S4\n\n"
            f"# [선택] 프로젝트 단위 Model Armor Floor Settings 확인\n"
            f"gcloud model-armor floorsettings describe --full-uri=\"projects/{project_id}/locations/global/floorSetting\""
        ),
        "terraform_snippet": (
            "# Agent Gateway for Cloud Run (Preview)\n"
            "# MCP·외부 도구 호출을 게이트웨이로 보내고, 초기에는 DRY_RUN으로 정책을 검증한 뒤 강제합니다.\n"
            "# 자세한 리소스 정의는 CR-S4 패치 결과(production_ready.tf)를 참고하세요."
        ),
        "console_guide": "MCP·외부 도구를 호출하는 에이전트라면 Agent Gateway for Cloud Run(Preview)으로 외부 호출을 통제하세요. 외부 도구 호출이 없으면 이 항목은 해당 없습니다.",
    })

    # 9. [R5.1] OpenTelemetry Prompt Privacy Guard (NO_CONTENT)
    otel_val = env_map.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "")
    otel_semconv = "OTEL_SEMCONV_STABILITY_OPT_IN" in env_map
    if otel_val == "NO_CONTENT":
        r51_status = "GREEN"
        r51_current = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT 설정됨 (프롬프트 평문 기록 차단)"
    elif otel_val:
        r51_status = "AMBER"
        r51_current = f"OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT={otel_val} (프로덕션 PII 취급 시 NO_CONTENT 변경 권고)"
    else:
        r51_status = "AMBER"
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

    verified_checks = [c for c in checks if c["status"] in ("GREEN", "AMBER", "RED")]
    green_cnt = sum(1 for c in checks if c["status"] == "GREEN")
    amber_cnt = sum(1 for c in checks if c["status"] == "AMBER")
    red_cnt = sum(1 for c in checks if c["status"] == "RED")
    review_cnt = sum(1 for c in checks if c["status"] == "REVIEW")

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
        "verdict": verdict,
        "total_count": len(checks),
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
            "functional_type": functional_type,
            "identity_type": identity_type,
            "is_mcp_server": is_mcp_server,
            "hardcoded_remote_urls": hardcoded_remote_urls,
        },
        "combined_runtime_fix_cmd": combined_gcloud,
        "platform_guarantees": [],
        "stage1_skill_cmd": (
            "# 배포 전 로컬 PC (Agent Skill): 체크리스트 v3 Cloud Run 항목(Features · Optimize · Delivery & Eval · Observability · Security) 점검 및 패치\n"
            "# 일반 플랫폼 Best Practice는 플랫폼 Skill 참고: https://github.com/google/skills/tree/main/skills/cloud\n"
            "uv run prod-ready scan <내_로컬_에이전트_코드_폴더> --target cloud_run"
        ),
        "checks": checks,
    }


def _evaluate_agent_runtime_engine_legacy(
    project_id: str,
    region: str,
    engine_id: str,
    eng: dict[str, Any],
    now_str: str,
) -> dict[str, Any]:
    spec = eng.get("spec", {})
    dep_spec = spec.get("deploymentSpec", {})
    sa_email = spec.get("serviceAccount") or spec.get("effectiveIdentity", "")
    identity_type = str(spec.get("identityType") or "").strip()
    has_agent_identity = identity_type.upper() == "AGENT_IDENTITY"
    disp_name = eng.get("displayName") or engine_id
    psc_iface = dep_spec.get("pscInterfaceConfig", {})
    agw_cfg = dep_spec.get("agentGatewayConfig", {})
    agw_egress = (agw_cfg.get("agentToAnywhereConfig") or {}).get("agentGateway", "")
    agw_ingress = (agw_cfg.get("clientToAgentConfig") or {}).get("agentGateway", "")
    has_agent_gateway = bool(agw_egress or agw_ingress)

    env_list = dep_spec.get("env", [])
    env_map = {e.get("name", ""): str(e.get("value", "")) for e in env_list if "name" in e}

    ar_remote_keys = ("MCP_SERVER_URL", "MCP_URL", "A2A_AGENT_URL", "A2A_SERVER_URL", "AGENT_CARD_URL")
    ar_hardcoded_remote_urls = [
        f"{k}={v}"
        for k, v in env_map.items()
        if (
            k.upper() in ar_remote_keys
            or k.upper().endswith(("_MCP_URL", "_MCP_SERVER_URL", "_A2A_URL", "_AGENT_CARD_URL"))
        )
        and v.strip().lower().startswith(("http://", "https://"))
    ]
    ar_r42_lazy_suffix = (
        f" · 💡 원격 MCP/A2A 정적 URL 환경변수 감지({', '.join(ar_hardcoded_remote_urls[:2])}) — "
        "ADK AgentRegistry(get_mcp_toolset / get_remote_a2a_agent) Lazy Discovery 전환 권장"
        if ar_hardcoded_remote_urls
        else ""
    )

    otel_capture = env_map.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "")
    loc_val = env_map.get("GOOGLE_CLOUD_LOCATION", "")
    has_guardrail = env_map.get("MODEL_ARMOR_ENABLED", "").lower() == "true" or "MODEL_ARMOR_TEMPLATE_ID" in env_map
    min_inst = dep_spec.get("minInstances")
    max_inst = dep_spec.get("maxInstances")

    ar_platform_guarantees = [
        (
            f"관리형 컨테이너 헬스체크 & 오토스케일링 (Managed Probes & Auto-Scaling): "
            f"Agent Runtime({spec.get('agentFramework', 'google-adk')}) 플랫폼이 헬스 프로브·컨테이너 수명주기·오토스케일링을 자동 관리합니다"
            + (f" (현재 minInstances={min_inst}, maxInstances={max_inst})" if (min_inst is not None or max_inst is not None) else "")
            + "."
        ),
        (
            f"[AR-F1 · AR-F2] 관리형 영속 세션 저장 · 관리형 장기 기억 저장 (VertexAiSessionService · VertexAiMemoryBankService): "
            f"별도 Cloud SQL/Redis 없이 agentengine:// 기반 영속 세션 및 장기 메모리를 플랫폼이 기본 제공합니다 "
            f"(현재 ADK_SESSION_SERVICE_URI={env_map.get('ADK_SESSION_SERVICE_URI', 'agentengine://')})."
        ),
        (
            "제로 트러스트 IAM 인바운드 인증 (IAM-Only Inbound): "
            "퍼블릭 무인증 웹 URL을 노출하지 않으며, Agent Platform IAM 권한이 있는 호출자만 허용합니다."
        ),
    ]

    checks = [
        {
            "id": "R3.2",
            "domain": "3. 네트워크 & 엣지 보안",
            "check_type": "ARCH_DECISION",
            "verification_scope": "ARCH_DECISION",
            "title": "Private Service Connect Interface (PSC-I) 필요 여부 확인 (사설 VPC 접근 시에만 필요)",
            "status": "GREEN" if psc_iface else "REVIEW",
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
            "domain": "4. 보안, 신원 & 거버넌스",
            "check_type": "RUNTIME_VERIFIED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "전용 최소 권한 서비스 계정 (Custom Service Account / Agent Identity)",
            "status": "GREEN" if (has_agent_identity or ("@gcp-sa-aiplatform-re" not in sa_email and sa_email)) else "AMBER",
            "current_state": (
                f"Agent Identity (AGENT_IDENTITY) 활성화됨 ({sa_email or 'SPIFFE Principal'})"
                if has_agent_identity
                else f"현재 ID: {sa_email or '기본 Reasoning Engine 공유 SA'}"
            ),
            "recommended_state": "에이전트별 전용 커스텀 서비스 계정 또는 Agent Identity (AGENT_IDENTITY) 할당",
            "why_it_matters": "여러 에이전트가 기본 Reasoning Engine 서비스 계정을 공유하면 에이전트별 최소 권한 분리가 어렵습니다.",
            "how_to_fix_title": "전용 커스텀 서비스 계정 또는 Agent Identity 지정",
            "gcloud_cmd": f"uvx google-agents-cli deploy --project={project_id} --region={region} --service-account=<CUSTOM_SA_EMAIL>",
            "terraform_snippet": 'identity_type = "AGENT_IDENTITY" # 또는 service_account = google_service_account.app_sa.email',
            "console_guide": "재배포 시 `--agent-identity` 또는 `--service-account` 플래그로 에이전트별 ID를 분리하세요.",
        },
        {
            "id": "R4.2",
            "domain": "4. 보안, 신원 & 거버넌스",
            "check_type": "RUNTIME_RECOMMENDED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "Agent Identity (SPIFFE ID) & Agent Registry 자동 등록",
            "status": "GREEN" if has_agent_identity else "AMBER",
            "current_state": (
                (
                    f"spec.identityType=AGENT_IDENTITY 확인됨 ({sa_email or 'SPIFFE ID 발급 및 Agent Registry 자동 등록'})"
                    if has_agent_identity
                    else f"spec.identityType={identity_type or 'SERVICE_ACCOUNT/미설정'} — 에이전트 전용 SPIFFE ID 미활성화"
                )
                + ar_r42_lazy_suffix
            ),
            "recommended_state": "spec.identityType = AGENT_IDENTITY + agentregistry.googleapis.com 활성화 (Agent Registry 자동 등록)",
            "why_it_matters": (
                "Agent Runtime에 `identity_type = \"AGENT_IDENTITY\"`를 활성화하면 에이전트별 고유 SPIFFE ID가 자동 프로비저닝되며 "
                "Agent Registry에 등록되어 엔터프라이즈 거버넌스 및 mTLS 기반 ID 바인딩을 지원합니다. "
                "(※ `AGENT_IDENTITY`와 `service_account`는 상호 배타적이므로 동시에 지정할 수 없습니다.)"
            ),
            "how_to_fix_title": "Agent Identity 활성화 및 Agent Registry 등록 확인",
            "gcloud_cmd": (
                f"# [Step 1] Agent Registry & App Hub API 활성화\n"
                f"gcloud services enable agentregistry.googleapis.com apphub.googleapis.com --project={project_id}\n\n"
                f"# [Step 2] Agent Identity 활성화 재배포 (※ --service-account 와 동시 지정 불가)\n"
                f"uvx google-agents-cli deploy --project={project_id} --region={region} --agent-identity\n\n"
                f"# [Step 3 · 읽기 전용 확인] Agent Registry 등록 조회\n"
                f"gcloud agent-registry agents list --location={region} --project={project_id}"
            ),
            "terraform_snippet": (
                'provider = google-beta\n'
                'spec {\n'
                '  identity_type = "AGENT_IDENTITY"\n'
                '}'
            ),
            "console_guide": f"Agent Registry 콘솔 또는 `gcloud agent-registry agents list --location={region} --project={project_id}`에서 등록 상태를 확인하세요.",
        },
        {
            "id": "R4.3",
            "domain": "4. 보안, 신원 & 거버넌스",
            "check_type": "RUNTIME_RECOMMENDED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "Agent Gateway (Egress/Ingress 중앙 정책 · UAP/IAP V2) & Model Armor",
            "status": "GREEN" if has_agent_gateway else "AMBER",
            "current_state": (
                f"Agent Gateway 바인딩 확인됨 (egress={agw_egress or '미설정'}, ingress={agw_ingress or '미설정'})"
                + (" · Model Armor env 감지됨" if has_guardrail else "")
                if has_agent_gateway
                else (
                    "deploymentSpec.agentGatewayConfig 미연결 (직접 Egress/Ingress 모드 — "
                    + ("Model Armor env 감지됨)" if has_guardrail else "중앙 Agent Gateway 미적용)")
                )
            ),
            "recommended_state": (
                "google_network_services_agent_gateway + UAP/IAP V2 (초기 iamEnforcementMode=DRY_RUN으로 HTTP 498 차단 방지) + "
                "BYOC Dockerfile AGENT_GATEWAY_ROOT_CERTIFICATES 주입"
            ),
            "why_it_matters": (
                "Agent Runtime의 `agentGatewayConfig`에 Agent Gateway를 연결하면 MCP·A2A·LLM 아웃바운드 호출에 대해 "
                "중앙 정책(UAP/IAP V2)과 Model Armor Floor Settings 검사를 코드 변경 없이 일괄 강제할 수 있습니다. "
                "(⚠️ 중요: 신규 Agent Gateway 연결 시 반드시 `iamEnforcementMode=DRY_RUN`으로 시작해 HTTP 498 차단을 방지하고, "
                "로그 검증 후 `ENFORCED`로 승격하세요. 커스텀 컨테이너는 `Dockerfile`에 `AGENT_GATEWAY_ROOT_CERTIFICATES` 주입이 필요합니다.)"
            ),
            "how_to_fix_title": "Agent Gateway(DRY_RUN) 프로비저닝 및 Agent Runtime 연결",
            "gcloud_cmd": (
                f"# [Step 1] 로컬 AR-S3 조치로 Agent Gateway Terraform(DRY_RUN) + Dockerfile CA 번들 생성\n"
                f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target agent_runtime --items AR-S3\n\n"
                f"# [Step 2] Model Armor Floor Settings 정책 확인 (읽기 전용)\n"
                f"gcloud model-armor floorsettings describe --full-uri=\"projects/{project_id}/locations/global/floorSetting\"\n\n"
                f"# [Step 3] Agent Gateway 연결 확인\n"
                f"gcloud beta network-services agent-gateways list --location={region} --project={project_id}"
            ),
            "terraform_snippet": (
                'agent_gateway_config {\n'
                '  agent_to_anywhere_config {\n'
                '    agent_gateway = google_network_services_agent_gateway.egress_gateway.id\n'
                '  }\n'
                '}'
            ),
            "console_guide": "Agent Skill(`--target agent_runtime --items AR-S3`)으로 `production_ready.tf`의 Agent Gateway(`iamEnforcementMode=DRY_RUN`)와 `Dockerfile` CA 번들 설치 구문을 생성한 뒤 배포하세요.",
        },
        {
            "id": "R5.1",
            "domain": "5. 관측성 & 프라이버시",
            "check_type": "RUNTIME_RECOMMENDED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "OpenTelemetry 트레이스 프롬프트(PII) 평문 기록 방지 (NO_CONTENT)",
            "status": "GREEN" if otel_capture == "NO_CONTENT" else "AMBER",
            "current_state": f"OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = {otel_capture or '미설정'} (프로덕션 PII 취급 시 NO_CONTENT 권고)",
            "recommended_state": "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = NO_CONTENT",
            "why_it_matters": f"현재 `{otel_capture or '미설정'}` 상태입니다. 프로덕션에서 고객 개인정보가 포함된 프롬프트 원문이 트레이스에 기록되지 않도록 `NO_CONTENT` 설정을 권장합니다.",
            "how_to_fix_title": "OTel 프라이버시 환경변수 NO_CONTENT 설정",
            "gcloud_cmd": "uv run prod-ready apply <내_로컬_에이전트_폴더> --target agent_runtime --items AR-OB1",
            "terraform_snippet": 'env {\n  name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\n  value = "NO_CONTENT"\n}',
            "console_guide": "배포 설정에서 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 값을 `NO_CONTENT`로 지정하세요.",
        },
    ]

    verified_checks = [c for c in checks if c["status"] in ("GREEN", "AMBER", "RED")]
    green_cnt = sum(1 for c in checks if c["status"] == "GREEN")
    amber_cnt = sum(1 for c in checks if c["status"] == "AMBER")
    red_cnt = sum(1 for c in checks if c["status"] == "RED")
    review_cnt = sum(1 for c in checks if c["status"] == "REVIEW")

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
        "verdict": verdict,
        "total_count": len(checks),
        "green_count": green_cnt,
        "amber_count": amber_cnt,
        "red_count": red_cnt,
        "review_count": review_cnt,
        "verified_count": len(verified_checks),
        "detected_config": {
            "min_instances": min_inst if min_inst is not None else 1,
            "max_instances": max_inst if max_inst is not None else 10,
            "location": loc_val or "global",
            "identity_type": identity_type,
            "has_agent_gateway": has_agent_gateway,
            "hardcoded_remote_urls": ar_hardcoded_remote_urls,
        },
        "combined_runtime_fix_cmd": (
            f"# ⚠️ 선택한 항목에 따라 UI에서 맞춤형 명령어가 동적으로 생성됩니다.\n"
            f"uvx google-agents-cli deploy --project={project_id} --region={region}"
        ),
        "platform_guarantees": ar_platform_guarantees,
        "stage1_skill_cmd": "uv run prod-ready scan <내_로컬_에이전트_코드_폴더> --target agent_runtime  # 체크리스트 v3 Agent Runtime 항목 (일반 플랫폼 BP는 플랫폼 Skill 참고)",
        "checks": checks,
    }


def _evaluate_gke_workload_legacy(
    project_id: str,
    region: str,
    workload_name: str,
    bundle: dict[str, Any],
    now_str: str,
) -> dict[str, Any]:
    """Evaluates a GKE AI Agent workload bundle (GkeAgentBundle, kubectl List, Deployment, or GKE Cluster JSON)
    in 100% Read-Only mode without arbitrary weighted scores.
    """
    cluster = bundle.get("cluster") or {}
    ns_obj = bundle.get("namespace") or {}
    deployment = bundle.get("deployment") or {}
    pdb = bundle.get("pdb")
    hpa = bundle.get("hpa")
    netpols = bundle.get("networkPolicies") or ([] if bundle.get("networkPolicy") is None else [bundle["networkPolicy"]])
    pod_monitoring = bundle.get("podMonitoring")
    sandbox = bundle.get("sandbox")
    substrate = bundle.get("substrate")
    agent_gateways = bundle.get("agentGateways") or ([] if bundle.get("agentGateway") is None else [bundle["agentGateway"]])

    has_cluster = bool(cluster)
    has_deployment = bool(deployment)

    # Cluster attributes
    cluster_name = str(cluster.get("name") or workload_name)
    master_version = str(cluster.get("currentMasterVersion") or "")
    is_autopilot = bool(cluster.get("autopilot", {}).get("enabled")) if has_cluster else None
    cluster_mode_label = (
        "GKE Autopilot" if is_autopilot is True else ("GKE Standard" if is_autopilot is False else "클러스터 JSON 미포함 (K8s 매니페스트 기준)")
    )
    net_cfg = cluster.get("networkConfig") or {}
    priv_cfg = cluster.get("privateClusterConfig") or {}
    is_private_nodes = bool(
        priv_cfg.get("enablePrivateNodes")
        or net_cfg.get("defaultEnablePrivateNodes")
        or (cluster.get("controlPlaneEndpointsConfig", {}).get("ipEndpointsConfig", {}).get("enabled") is False)
    )
    is_dataplane_v2 = (
        str(net_cfg.get("datapathProvider", "")).upper() == "ADVANCED_DATAPATH"
        or bool(is_autopilot)
    )
    workload_pool = str(cluster.get("workloadIdentityConfig", {}).get("workloadPool", ""))

    # Deployment & Container attributes
    dep_meta = deployment.get("metadata", {}) if isinstance(deployment, dict) else {}
    dep_spec = deployment.get("spec", {}) if isinstance(deployment, dict) else {}
    pod_template = dep_spec.get("template", {}) if isinstance(dep_spec, dict) else {}
    pod_meta = pod_template.get("metadata", {}) if isinstance(pod_template, dict) else {}
    pod_spec = pod_template.get("spec", {}) if isinstance(pod_template, dict) else {}
    containers = pod_spec.get("containers", []) if isinstance(pod_spec, dict) else []
    c0 = containers[0] if containers and isinstance(containers[0], dict) else {}

    namespace_name = str(
        dep_meta.get("namespace")
        or ns_obj.get("metadata", {}).get("name")
        or "default"
    )
    replicas = dep_spec.get("replicas")
    runtime_class = str(pod_spec.get("runtimeClassName", ""))
    ksa_name = str(pod_spec.get("serviceAccountName", ""))
    automount_sa = pod_spec.get("automountServiceAccountToken")

    res_req = c0.get("resources", {}).get("requests", {})
    res_lim = c0.get("resources", {}).get("limits", {})
    cpu_req = str(res_req.get("cpu", res_lim.get("cpu", "미지정")))
    mem_req = str(res_req.get("memory", res_lim.get("memory", "미지정")))

    env_list = c0.get("env", []) if isinstance(c0.get("env"), list) else []
    env_map: dict[str, str] = {}
    for e in env_list:
        if isinstance(e, dict) and e.get("name"):
            if "value" in e:
                env_map[str(e["name"])] = str(e.get("value", ""))
            elif "valueFrom" in e:
                env_map[str(e["name"])] = "<Secret/ConfigMap>"

    # Probes
    startup_probe = c0.get("startupProbe") or {}
    readiness_probe = c0.get("readinessProbe") or {}
    liveness_probe = c0.get("livenessProbe") or {}
    probes_present = [p for p in (startup_probe, readiness_probe, liveness_probe) if p]
    uses_http_probe = any("httpGet" in p for p in probes_present)
    uses_tcp_probe = any("tcpSocket" in p for p in probes_present) and not uses_http_probe
    http_path = ""
    for p in (readiness_probe, startup_probe, liveness_probe):
        if isinstance(p, dict) and isinstance(p.get("httpGet"), dict):
            http_path = str(p["httpGet"].get("path", "/"))
            break

    # Security context & Namespace PSS
    pod_sc = pod_spec.get("securityContext") or {}
    ctr_sc = c0.get("securityContext") or {}
    run_as_non_root = bool(pod_sc.get("runAsNonRoot") or ctr_sc.get("runAsNonRoot"))
    no_priv_esc = ctr_sc.get("allowPrivilegeEscalation") is False
    drop_caps = [str(x).upper() for x in (ctr_sc.get("capabilities", {}).get("drop") or [])]
    drops_all = "ALL" in drop_caps
    seccomp_type = str(
        (pod_sc.get("seccompProfile") or {}).get("type")
        or (ctr_sc.get("seccompProfile") or {}).get("type")
        or ""
    )
    has_hardened_sc = run_as_non_root and no_priv_esc and drops_all and seccomp_type == "RuntimeDefault"

    ns_labels = (ns_obj.get("metadata", {}) or {}).get("labels", {}) if isinstance(ns_obj, dict) else {}
    pss_enforce = str(ns_labels.get("pod-security.kubernetes.io/enforce", ""))
    has_pss_restricted = pss_enforce == "restricted"
    has_dedicated_ksa = bool(ksa_name and ksa_name != "default")

    # Agent Identity & Registry labels
    all_labels: dict[str, str] = {}
    for src in (dep_meta.get("labels"), pod_meta.get("labels")):
        if isinstance(src, dict):
            all_labels.update({str(k): str(v) for k, v in src.items()})
    all_annotations: dict[str, str] = {}
    for src in (dep_meta.get("annotations"), pod_meta.get("annotations")):
        if isinstance(src, dict):
            all_annotations.update({str(k): str(v) for k, v in src.items()})
    has_registry_label = (
        all_labels.get("registry.gke.io/functional-type", "").upper() == "AGENT"
        or all_annotations.get("registry.gke.io/functional-type", "").upper() == "AGENT"
    )
    spiffe_identity_type = (
        all_annotations.get("iam.gke.io/spiffe-identity-type")
        or all_annotations.get("iam.gke.io/identity")
        or ""
    )
    has_spiffe_agent_identity = spiffe_identity_type.lower() in ("agent-identity", "agent_identity", "agent")

    hardcoded_remote_urls = [
        f"{k}={v}"
        for k, v in env_map.items()
        if any(tok in k.upper() for tok in ("MCP_SERVER", "MCP_URL", "A2A_", "REMOTE_AGENT"))
        and v.startswith(("http://", "https://"))
    ]

    # NetworkPolicy structural analysis (do NOT confuse `ipBlock.except` with allowed CIDRs)
    has_egress_netpol = False
    allows_metadata_ip = False
    blocks_metadata_ip = False
    allows_nodelocal_dns = False
    for np in netpols:
        if not isinstance(np, dict):
            continue
        kind_np = str(np.get("kind", ""))
        spec_np = np.get("spec") or {}
        ptypes = [str(p) for p in (spec_np.get("policyTypes") or [])]
        if kind_np == "FQDNNetworkPolicy" or "Egress" in ptypes or "egress" in spec_np:
            has_egress_netpol = True
            for rule in (spec_np.get("egress") or []):
                if not isinstance(rule, dict):
                    continue
                for to_entry in (rule.get("to") or []):
                    if not isinstance(to_entry, dict):
                        continue
                    ip_block = to_entry.get("ipBlock")
                    if not isinstance(ip_block, dict):
                        continue
                    cidr = str(ip_block.get("cidr", ""))
                    excepts = [str(x) for x in (ip_block.get("except") or [])]
                    if cidr.startswith(("169.254.169.254", "169.254.169.252")):
                        allows_metadata_ip = True
                    if cidr.startswith("169.254.20.10"):
                        allows_nodelocal_dns = True
                    if any(ex.startswith("169.254.169.254") for ex in excepts):
                        blocks_metadata_ip = True

    # Execution pattern detection (Standard Pods vs Agent Sandbox vs Agent Substrate)
    is_sandbox_pattern = bool(sandbox) or runtime_class.lower() in ("gvisor", "kata")
    is_substrate_pattern = bool(substrate)
    autopilot_substrate_conflict = (is_autopilot is True) and is_substrate_pattern
    if is_substrate_pattern:
        exec_pattern = "agent_substrate"
        exec_pattern_label = "Agent Substrate on GKE (ate.dev/v1alpha1)"
    elif is_sandbox_pattern:
        exec_pattern = "agent_sandbox"
        exec_pattern_label = "Agent Sandbox on GKE (샌드박스 격리)"
    else:
        exec_pattern = "standard_pods"
        exec_pattern_label = "Standard GKE Pods (ADK Deployment)"

    has_agent_gateway = bool(agent_gateways)

    # Session persistence (require non-empty value so AGENT_ENGINE_SESSION_ID="" does not falsely pass)
    session_env_keys = [
        k for k, v in env_map.items()
        if str(v).strip()
        and any(
            tok in k.upper()
            for tok in (
                "SESSION_SERVICE_URI",
                "AGENT_ENGINE_SESSION",
                "AGENT_ENGINE_ID",
                "DATABASE_URL",
                "REDIS_HOST",
                "ALLOYDB",
                "POSTGRES",
            )
        )
    ]
    has_persistent_session = bool(session_env_keys)

    # Observability & Privacy
    otel_capture = env_map.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "")
    adk_capture_spans = env_map.get("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "").lower()
    otel_privacy_ok = (otel_capture == "NO_CONTENT") and (adk_capture_spans != "true")
    has_pod_monitoring = bool(pod_monitoring)

    # FinOps / Gemini Location
    loc_val = env_map.get("GOOGLE_CLOUD_LOCATION", "")

    # R1.1 status (respect HPA minReplicas + check multi-zone topologySpreadConstraints)
    has_pdb = bool(pdb)
    has_hpa = bool(hpa)
    hpa_min_replicas = ((hpa or {}).get("spec") or {}).get("minReplicas") if isinstance(hpa, dict) else None
    effective_replicas = (
        max(
            int(replicas) if isinstance(replicas, int) else 0,
            int(hpa_min_replicas) if isinstance(hpa_min_replicas, int) else 0,
        )
        or 1
    )
    topo_spreads = pod_spec.get("topologySpreadConstraints") or []
    has_zone_spread = any(
        isinstance(ts, dict) and str(ts.get("topologyKey", "")) == "topology.kubernetes.io/zone"
        for ts in topo_spreads
    )
    grace_sec_raw = pod_spec.get("terminationGracePeriodSeconds")
    grace_sec = int(grace_sec_raw) if isinstance(grace_sec_raw, int) else 30
    has_pre_stop = bool(((c0.get("lifecycle") or {}).get("preStop")) if isinstance(c0, dict) else False)
    has_graceful_drain = (grace_sec >= 60) and has_pre_stop

    if has_deployment:
        if effective_replicas >= 2 and has_pdb and has_hpa and has_zone_spread:
            r11_status = "GREEN"
        else:
            r11_status = "AMBER"
    else:
        r11_status = "REVIEW"

    r13_status = "GREEN" if has_graceful_drain else ("AMBER" if has_deployment else "REVIEW")

    # R3.1 status
    if (is_sandbox_pattern or is_substrate_pattern) and allows_metadata_ip:
        r31_status = "RED"
    elif not is_sandbox_pattern and not is_substrate_pattern and blocks_metadata_ip and not allows_metadata_ip:
        # Standard WI Pod with 169.254.169.254 blocked via `except` breaks Workload Identity token minting
        r31_status = "AMBER"
    elif has_egress_netpol and (not has_cluster or (is_private_nodes and is_dataplane_v2)):
        r31_status = "GREEN"
    elif has_cluster and not is_private_nodes and not has_egress_netpol:
        r31_status = "RED"
    else:
        r31_status = "AMBER"

    # R4.1 status (cluster-only JSON cannot verify PSS/securityContext -> REVIEW, never False GREEN)
    if has_deployment:
        if has_hardened_sc and has_dedicated_ksa and (has_pss_restricted or not ns_obj):
            r41_status = "GREEN" if has_pss_restricted else "AMBER"
        elif not run_as_non_root or not drops_all or not no_priv_esc:
            r41_status = "RED"
        else:
            r41_status = "AMBER"
    else:
        r41_status = "REVIEW" if workload_pool else "AMBER"

    # GKE-S1 status (dedicated agent identity only; PSS / Non-Root are delegated to platform skills)
    if has_deployment:
        s1_status = "GREEN" if (has_dedicated_ksa or has_spiffe_agent_identity) else "RED"
    else:
        s1_status = "REVIEW" if workload_pool else "AMBER"

    # R4.2 status
    if not has_deployment:
        r42_status = "REVIEW"
    elif has_registry_label and not hardcoded_remote_urls:
        r42_status = "GREEN"
    else:
        r42_status = "AMBER"

    # R4.3 status
    if autopilot_substrate_conflict:
        r43_status = "RED"
    elif has_agent_gateway or is_sandbox_pattern or is_substrate_pattern:
        r43_status = "GREEN"
    else:
        r43_status = "AMBER"

    # R5.1 status (unified OTel NO_CONTENT privacy check; no custom PodMonitoring CRD required)
    if not has_deployment:
        r51_status = "REVIEW"
    else:
        r51_status = "GREEN" if otel_privacy_ok else "AMBER"

    checks = [
        {
            "id": "R1.1",
            "domain": "1. 컴퓨트 & 탄력성",
            "check_type": "RUNTIME_RECOMMENDED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "클러스터 모드 (Autopilot / Standard) & 가용성 (Replicas · 멀티존 분산 · PDB · HPA v2)",
            "status": r11_status,
            "current_state": (
                f"{cluster_mode_label}"
                + (f" ({master_version})" if master_version else "")
                + (
                    f" · replicas={replicas if replicas is not None else f'HPA 관리(min={hpa_min_replicas})' if hpa_min_replicas is not None else '미지정'}, "
                    f"멀티존 분산(topologySpreadConstraints)={'설정됨' if has_zone_spread else '미설정'}, "
                    f"PDB={'설정됨' if has_pdb else '미설정'}, HPA={'설정됨' if has_hpa else '미설정'}"
                    if has_deployment
                    else " · Deployment/PDB/HPA 매니페스트 미포함"
                )
            ),
            "recommended_state": "기존 클러스터 모드(Autopilot/Standard) 유지 + replicas(또는 HPA minReplicas) >= 2 + topologySpreadConstraints(zone) + PDB(minAvailable: 1) + HPA v2",
            "why_it_matters": (
                "단일 레플리카이거나 PDB·멀티존 분산(`topologySpreadConstraints`)이 없는 GKE 워크로드는 "
                "특정 가용 영역(Zone) 장애나 노드 업그레이드 시 서비스 전면 중단 위험이 있습니다. "
                "기존 클러스터 모드(Autopilot vs Standard)는 유지한 채 Zone 분산 + PDB + HPA v2를 함께 구성하세요."
            ),
            "how_to_fix_title": "멀티존 분산(topologySpreadConstraints) · PDB · HPA v2 적용 (기존 클러스터 모드 유지)",
            "gcloud_cmd": (
                f"# 플랫폼 Skill 참고: gke-reliability · gke-productionize (https://github.com/google/skills/tree/main/skills/cloud)\n"
                f"# [읽기 전용 확인] 현재 PDB / HPA 상태 조회\n"
                f"kubectl get deploy,pdb,hpa -n {namespace_name}"
            ),
            "terraform_snippet": (
                'topology_spread_constraint {\n'
                '  max_skew           = 1\n'
                '  topology_key       = "topology.kubernetes.io/zone"\n'
                '  when_unsatisfiable = "ScheduleAnyway"\n'
                '}\n'
                'resource "kubernetes_pod_disruption_budget_v1" "agent_pdb" {\n'
                '  metadata { name = "agent-pdb"; namespace = "' + namespace_name + '" }\n'
                '  spec { min_available = "1" }\n'
                '}'
            ),
            "console_guide": f"GKE 워크로드 콘솔 또는 `kubectl get deploy,pdb,hpa -n {namespace_name}`에서 멀티존 분산·PDB·HPA v2 적용 여부를 확인하세요.",
        },
        {
            "id": "R1.2",
            "domain": "1. 컴퓨트 & 탄력성",
            "check_type": "ARCH_DECISION",
            "verification_scope": "ARCH_DECISION",
            "title": "Pod CPU / 메모리 리소스 Requests·Limits 사이징 (워크로드 기준 확인)",
            "status": "REVIEW",
            "current_state": (
                f"requests/limits: CPU={cpu_req}, Memory={mem_req} ({exec_pattern_label})"
                if has_deployment
                else "Deployment 컨테이너 리소스 정보 없음 (클러스터 JSON만 입력됨)"
            ),
            "recommended_state": "트래픽·LLM 도구 메모리 프로파일에 맞춰 운영자가 직접 유지 또는 조정 (자동 상향 금지)",
            "why_it_matters": (
                "GKE Autopilot은 요청한 CPU/Memory에 비례해 과금되며, GKE Standard는 노드 풀 적재율에 직결됩니다. "
                "OOMKill이나 CPU 스로틀링 로그가 없는 한 현재 사이징을 임의로 상향하지 말고 실측 기반으로 조정하세요."
            ),
            "how_to_fix_title": "실측 메모리·CPU 사용량 기반 유지 또는 선택적 조정",
            "gcloud_cmd": f"# [읽기 전용 확인] 실제 Pod CPU/Memory 사용량 점검\nkubectl top pods -n {namespace_name}",
            "terraform_snippet": "# 기존 containers.resources.requests / limits 값은 의도적으로 보존합니다.",
            "console_guide": "Cloud Monitoring(GKE Workloads)에서 OOMKilled 여부와 CPU 요청 대비 사용률을 확인한 뒤 필요한 경우에만 조정하세요.",
        },
        {
            "id": "R1.3",
            "domain": "Optimize",
            "check_type": "RUNTIME_RECOMMENDED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "무중단 종료 (긴 응답 보호 · `terminationGracePeriodSeconds` · `preStop`)",
            "status": r13_status,
            "current_state": (
                f"terminationGracePeriodSeconds={grace_sec}s, preStop={'설정됨' if has_pre_stop else '미설정'}"
                + (f" · readiness 프로브: HTTP GET({http_path or '/'})" if uses_http_probe else "")
                if has_deployment
                else "Deployment 매니페스트 미포함"
            ),
            "recommended_state": "terminationGracePeriodSeconds >= 최대 응답 시간(예: 120s) + preStop(sleep 10) + readiness 프로브로 종료 중 신규 요청 차단",
            "why_it_matters": (
                "에이전트 한 턴은 LLM 스트리밍과 도구 호출로 수십 초 이상 걸립니다. GKE 기본 종료 유예 시간(30초)과 `preStop` 훅이 없으면 "
                "롤링 업데이트나 노드 업그레이드 중 진행 중인 SSE 스트리밍·장시간 A2A/MCP 도구 호출이 SIGKILL로 잘립니다. "
                "`terminationGracePeriodSeconds`를 최대 응답 시간 이상으로 두고 `preStop`으로 진행 중인 응답을 마친 뒤 종료하세요. "
                "(헬스 프로브 구성 자체는 일반 플랫폼 Best Practice이므로 gke-reliability 플랫폼 Skill을 참고하세요.)"
            ),
            "how_to_fix_title": "Graceful Termination(120s + preStop) 적용",
            "gcloud_cmd": "uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-O1",
            "terraform_snippet": (
                'termination_grace_period_seconds = 120\n'
                'container {\n'
                '  lifecycle {\n'
                '    pre_stop { exec { command = ["/bin/sh", "-c", "sleep 10"] } }\n'
                '  }\n'
                '}'
            ),
            "console_guide": "Deployment에 `terminationGracePeriodSeconds: 120`과 `lifecycle.preStop` 훅을 구성해 진행 중인 응답을 보호하세요.",
        },
        {
            "id": "R2.1",
            "domain": "2. 세션 & 상태 영속성",
            "check_type": "RUNTIME_RECOMMENDED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "멀티턴 영속 세션 (`AGENT_ENGINE_SESSION_ID` / Cloud SQL / Redis)",
            "status": "GREEN" if has_persistent_session else ("AMBER" if has_deployment else "REVIEW"),
            "current_state": (
                f"영속 세션 환경변수 감지됨: {', '.join(session_env_keys)}"
                if has_persistent_session
                else (
                    "영속 세션 환경변수 미감지(또는 빈 값) — Pod 재시작·HPA 스케일아웃 시 인메모리 대화 문맥 유실 위험"
                    if has_deployment
                    else "Deployment 환경변수 미포함"
                )
            ),
            "recommended_state": "Agent Platform Managed Sessions (`AGENT_ENGINE_SESSION_ID` + `GOOGLE_CLOUD_AGENT_ENGINE_LOCATION`) 또는 Cloud SQL/AlloyDB/Memorystore 세션 백엔드 연동",
            "why_it_matters": (
                "GKE Pod는 오토스케일·롤링 업데이트·노드 유지보수로 수시로 교체됩니다. "
                "인메모리 세션에 의존하면 멀티턴 대화가 끊어지므로 외부 영속 세션 저장소를 연동해야 합니다."
            ),
            "how_to_fix_title": "외부 영속 세션 서비스 연동",
            "gcloud_cmd": "uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-F1",
            "terraform_snippet": (
                'env { name = "AGENT_ENGINE_SESSION_ID"; value = var.agent_engine_session_id }\n'
                'env { name = "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION"; value = var.region }'
            ),
            "console_guide": "ADK `SessionService`를 Agent Platform Managed Sessions (`VertexAiSessionService`) 또는 Cloud SQL/AlloyDB 백엔드로 구성하세요.",
        },
        {
            "id": "R3.1",
            "domain": "3. 네트워크 & 엣지 보안",
            "check_type": "RUNTIME_VERIFIED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "Private Nodes & Dataplane V2 Default-Deny Egress `NetworkPolicy`",
            "status": r31_status,
            "current_state": (
                f"Private Nodes={'활성' if is_private_nodes else ('미활성/미확인' if has_cluster else '클러스터 JSON 미포함')}, "
                f"Dataplane V2={'활성' if is_dataplane_v2 else ('미확인' if has_cluster else '미확인')}, "
                f"Egress NetworkPolicy={'설정됨' if has_egress_netpol else '미설정'}"
                + (
                    " (⚠️ Sandbox/Substrate에서 Metadata 169.254.169.254 허용 감지 — 즉시 차단 필요!)"
                    if (is_sandbox_pattern or is_substrate_pattern) and allows_metadata_ip
                    else (
                        " (GKE Metadata 169.254.169.254 차단 확인)"
                        if blocks_metadata_ip
                        else (
                            " (Workload Identity Metadata 169.254.169.254"
                            + (" + NodeLocal DNSCache 169.254.20.10" if allows_nodelocal_dns else "")
                            + " 허용 포함)"
                            if allows_metadata_ip
                            else ""
                        )
                    )
                )
            ),
            "recommended_state": (
                "Private Nodes + Dataplane V2 + Default-Deny Egress NetworkPolicy "
                "(Standard WI Pod: NodeLocal DNS 169.254.20.10:53 + DPv2 Metadata 169.254.169.254:80,8080 + non-DPv2 169.254.169.252:987,988 허용 / "
                "Sandbox·Substrate Pod: 169.254.169.254/32, 169.254.169.252/32 및 RFC 1918 차단)"
            ),
            "why_it_matters": (
                "AI 에이전트 프롬프트 인젝션으로 인한 SSRF 및 데이터 유출을 막으려면 Default-Deny Egress `NetworkPolicy`가 필수입니다. "
                "⚠️ 단, Standard Workload Identity Pod는 `169.254.20.10/32`(NodeLocal DNSCache), `169.254.169.254/32` TCP `80/8080`(DPv2 Metadata Server) & UDP/TCP `53`(Cloud DNS), "
                "`169.254.169.252/32` TCP `987/988`(비-DPv2)을 허용해야 DNS·ADC 토큰 발급이 깨지지 않으며, "
                "반대로 비신뢰 코드를 실행하는 Agent Sandbox/Substrate Pod는 `169.254.169.254/32`, `169.254.169.252/32`, 사설 IP(RFC 1918)를 완전 차단하고 `automountServiceAccountToken: false`를 적용해야 합니다."
            ),
            "how_to_fix_title": "Dataplane V2 Default-Deny Egress NetworkPolicy 적용",
            "gcloud_cmd": (
                f"# 플랫폼 Skill 참고: gke-networking · gke-workload-security (https://github.com/google/skills/tree/main/skills/cloud)\n"
                f"# [읽기 전용 확인] 적용된 NetworkPolicy 조회\n"
                f"kubectl get networkpolicy -n {namespace_name} -o yaml"
            ),
            "terraform_snippet": (
                'resource "kubernetes_network_policy_v1" "agent_egress_policy" {\n'
                '  spec {\n'
                '    policy_types = ["Egress"]\n'
                '    # Standard WI Pod: kube-system DNS(53,5353) + NodeLocal DNS(169.254.20.10:53) +\n'
                '    #   DPv2 Metadata(169.254.169.254:80,8080) + non-DPv2(169.254.169.252:987,988) + HTTPS(443)\n'
                '  }\n'
                '}'
            ),
            "console_guide": f"`kubectl get networkpolicy -n {namespace_name}`에서 Default-Deny Egress 규칙과 Metadata Server 예외/차단 정책이 실행 패턴과 일치하는지 확인하세요.",
        },
        {
            "id": "R4.1",
            "domain": "Security",
            "check_type": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "에이전트 전용 신원 · 최소 권한 (Agent Identity / Workload Identity 전용 KSA)",
            "status": s1_status,
            "current_state": (
                f"KSA={ksa_name or 'default'}"
                + (f", SPIFFE Agent Identity=`{spiffe_identity_type}`" if has_spiffe_agent_identity else "")
                + (f", WorkloadPool={workload_pool}" if workload_pool else ", Workload Identity Pool 미확인")
                if has_deployment
                else f"Workload Identity Pool={workload_pool or '미활성'} (Deployment 매니페스트 미포함 — 전용 KSA 확인 필요)"
            ),
            "recommended_state": "Agent Identity(Preview)로 에이전트 고유 신원 부여, 사용할 수 없으면 Workload Identity로 전용 KSA↔서비스 계정 연결 + 필요한 최소 역할만 부여",
            "why_it_matters": (
                "에이전트는 스스로 판단해 도구·API를 호출하므로, 권한이 넓으면 프롬프트 인젝션 한 번으로도 피해가 커집니다. "
                "Pod가 `default` KSA나 노드 기본 서비스 계정이 아닌 에이전트 전용 신원으로 실행되고 필요한 권한만 갖는지 확인하세요."
            ),
            "how_to_fix_title": "에이전트 전용 KSA + Workload Identity(또는 Agent Identity) 연결",
            "gcloud_cmd": (
                f"# [Step 1] 로컬 GKE 매니페스트에 전용 KSA + Workload Identity 바인딩 적용\n"
                f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-S1\n\n"
                f"# [Step 2 · 읽기 전용 확인] Deployment의 serviceAccountName 확인\n"
                f"kubectl get deploy -n {namespace_name} -o jsonpath='{{.items[*].spec.template.spec.serviceAccountName}}'"
            ),
            "terraform_snippet": (
                'resource "kubernetes_service_account_v1" "agent_ksa" {\n'
                '  metadata {\n'
                '    name        = "agent-ksa"\n'
                '    annotations = { "iam.gke.io/gcp-service-account" = google_service_account.agent_sa.email }\n'
                '  }\n'
                '}'
            ),
            "console_guide": f"`kubectl get deploy -n {namespace_name} -o yaml`에서 `serviceAccountName`이 에이전트 전용 KSA인지, 연결된 서비스 계정에 과도한 역할이 없는지 점검하세요.",
        },
        {
            "id": "R4.1P",
            "domain": "플랫폼 Skill 참조 권장",
            "check_type": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "Pod Security Standards `restricted` · Non-Root `securityContext`",
            "status": r41_status,
            "current_state": (
                f"PSS enforce={pss_enforce or '미설정'}, "
                f"runAsNonRoot={run_as_non_root}, allowPrivilegeEscalation=false:{no_priv_esc}, drop ALL:{drops_all}, seccomp={seccomp_type or '미설정'}"
                if has_deployment
                else "Deployment/Namespace 매니페스트 미포함 — PSS·securityContext 확인 필요"
            ),
            "recommended_state": (
                "Namespace `pod-security.kubernetes.io/enforce=restricted` + "
                "`runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`"
            ),
            "why_it_matters": (
                "일반 GKE 워크로드 보안 Best Practice로, 체크리스트 범위 밖의 참고 항목입니다. gke-workload-security 플랫폼 Skill을 참고하세요. "
                "(⚠️ 기존 Pod가 거부되지 않도록 Non-Root `securityContext`를 먼저 배포한 뒤 Namespace PSS `restricted` 라벨을 부여하세요.)"
            ),
            "how_to_fix_title": "플랫폼 Skill(gke-workload-security) 참고",
            "gcloud_cmd": (
                f"# 플랫폼 Skill 참고: gke-workload-security (https://github.com/google/skills/tree/main/skills/cloud)\n"
                f"# [읽기 전용 확인] Namespace PSS 라벨 확인\n"
                f"kubectl get ns {namespace_name} --show-labels"
            ),
            "terraform_snippet": (
                'security_context {\n'
                '  run_as_non_root            = true\n'
                '  allow_privilege_escalation = false\n'
                '  capabilities { drop = ["ALL"] }\n'
                '  seccomp_profile { type = "RuntimeDefault" }\n'
                '}'
            ),
            "console_guide": f"`kubectl get ns {namespace_name} --show-labels`와 Deployment의 `securityContext`를 점검하세요.",
        },
        {
            "id": "R4.2",
            "domain": "4. 보안, 신원 & 거버넌스",
            "check_type": "RUNTIME_RECOMMENDED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "GKE Agent Identity (SPIFFE) & Agent Registry (`registry.gke.io/functional-type: AGENT`)",
            "status": r42_status,
            "current_state": (
                (
                    f"Agent Registry 라벨 확인됨 (`registry.gke.io/functional-type=AGENT`"
                    + (f", SPIFFE=`{spiffe_identity_type}`)" if has_spiffe_agent_identity else ", Pod SPIFFE annotation 미감지)")
                    if has_registry_label
                    else (
                        "Deployment에 `registry.gke.io/functional-type: AGENT` 라벨 미설정 (Agent Registry 자동 등록 미활성)"
                        if has_deployment
                        else "Deployment 매니페스트 미포함 (Agent Registry 라벨 확인 필요)"
                    )
                )
                + (
                    f" · ⚠️ 정적 원격 URL 환경변수 감지({', '.join(hardcoded_remote_urls)}) → Agent Registry Lazy Discovery 전환 권장"
                    if hardcoded_remote_urls
                    else ""
                )
            ),
            "recommended_state": (
                "Deployment 라벨 `registry.gke.io/functional-type: AGENT` + Pod 어노테이션 `iam.gke.io/spiffe-identity-type: agent-identity` + "
                "`agentregistry.googleapis.com` 활성화 + 원격 MCP/A2A URL은 Agent Registry 동적 조회"
            ),
            "why_it_matters": (
                "GKE Deployment에 공식 라벨 `registry.gke.io/functional-type: \"AGENT\"`과 Pod 어노테이션 `iam.gke.io/spiffe-identity-type: \"agent-identity\"`를 부여하면 "
                "Cloud Agent Registry와 SPIFFE 기반 GKE Agent Identity가 워크로드를 자동 식별하여 엔터프라이즈 거버넌스와 동적 MCP/A2A 검색(Lazy Discovery)을 지원합니다."
            ),
            "how_to_fix_title": "Agent Registry 공식 라벨·SPIFFE 어노테이션 추가 및 API 활성화",
            "gcloud_cmd": (
                f"# [Step 1] Agent Registry & App Hub API 활성화\n"
                f"gcloud services enable agentregistry.googleapis.com apphub.googleapis.com --project={project_id}\n\n"
                f"# [Step 2] 로컬 GKE 코드에 Agent Registry 라벨 + SPIFFE 어노테이션 자동 추가\n"
                f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-S3\n\n"
                f"# [Step 3 · 읽기 전용 확인] Agent Registry 등록 상태 조회\n"
                f"gcloud agent-registry agents list --location={region} --project={project_id}"
            ),
            "terraform_snippet": (
                '# Deployment metadata.labels (공식 자동 등록 라벨)\n'
                'labels = { "registry.gke.io/functional-type" = "AGENT" }\n'
                '# Pod template metadata.annotations (GKE Agent Identity SPIFFE)\n'
                'annotations = { "iam.gke.io/spiffe-identity-type" = "agent-identity" }'
            ),
            "console_guide": "Deployment 라벨에 `registry.gke.io/functional-type: AGENT`, Pod 어노테이션에 `iam.gke.io/spiffe-identity-type: agent-identity`를 추가하고 Agent Registry 콘솔에서 자동 등록을 확인하세요.",
        },
        {
            "id": "R4.3",
            "domain": "Security",
            "check_type": "RUNTIME_RECOMMENDED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "Agent Gateway for GKE (외부 호출 통제 · Preview)",
            "status": r43_status,
            "current_state": (
                f"실행 패턴: {exec_pattern_label}"
                + (" · 🔴 호환 불가: GKE Autopilot에서는 Agent Substrate를 사용할 수 없습니다 (GKE Standard >= 1.37 전용)" if autopilot_substrate_conflict else "")
                + (f" · Agent Gateway for GKE 리소스 {len(agent_gateways)}건 감지됨" if has_agent_gateway else " · Agent Gateway for GKE 리소스 미감지")
                + (f" · automountServiceAccountToken={automount_sa}" if is_sandbox_pattern or is_substrate_pattern else "")
            ),
            "recommended_state": (
                "MCP·외부 도구를 호출하는 에이전트: Agent Gateway for GKE(Preview, GKE >= 1.37.2-gke.1042000 Rapid, Standard Pods)로 외부 호출에 접근 정책 적용 / "
                "AI 생성 코드를 실행한다면 GKE-S2 Agent Substrate on GKE 샌드박스(상시 실행 · 사이드카 · Arm · Autopilot이 필요하면 Agent Sandbox on GKE) 함께 검토"
            ),
            "why_it_matters": (
                "에이전트가 무엇을 호출할지는 LLM이 실행 시점에 정하므로, 코드 리뷰만으로는 외부 호출을 통제할 수 없습니다.\n"
                "• **Agent Gateway for GKE** (`networking.gke.io/v1`, Preview): Ingress(`Gateway` + `HTTPRoute` + `GCPAgentGateway` + `GCPAuthzPolicy` `enforcementLevel: L7` + `GCPExtensionBinding` Model Armor)와 "
                "Egress(`networking.gke.io/dataplane-mode=ambient` + `GCPBackend` + `GCPEgressPolicy`)로 에이전트 트래픽을 중앙 통제합니다.\n"
                "• **GKE-S2 · Agent Substrate on GKE** (`ate.dev/v1alpha1`): AI 생성 코드를 커널·네트워크 격리된 샌드박스에서 실행합니다 (GKE Standard >= 1.37 전용). "
                "상시 실행 에이전트, 사이드카 등 전체 PodSpec, Arm 노드, **Autopilot**이 필요하면 **Agent Sandbox on GKE**를 사용합니다 (Agent Substrate는 GKE Standard 1.37 이상·x86 전용)."
            ),
            "how_to_fix_title": "Agent Gateway for GKE 매니페스트 생성 (AI 생성 코드 실행 시 GKE-S2 함께)",
            "gcloud_cmd": (
                f"# [Option A] Standard GKE Pods + Agent Gateway for GKE 매니페스트 생성\n"
                f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-S4\n"
                f"# GKE >= 1.37.2-gke.1042000 Rapid + 필요한 API + Proxy-only Subnet 확인 후 적용:\n"
                f"kubectl apply -f deployment/k8s/agent-gateway.yaml\n\n"
                f"# [Option B] AI 생성 코드 실행: Agent Substrate on GKE (GKE Standard >= 1.37 전용)\n"
                f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-S2 \\\n"
                f"  --params '{{\"gke_cluster_mode\":\"standard\",\"gke_execution_pattern\":\"agent_substrate\"}}'\n\n"
                f"# [Option C] 상시 실행 에이전트 · 사이드카 · Arm 노드 · Autopilot이 필요할 때: Agent Sandbox on GKE\n"
                f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-S2 \\\n"
                f"  --params '{{\"gke_execution_pattern\":\"agent_sandbox\"}}'"
            ),
            "terraform_snippet": (
                "# deployment/k8s/agent-gateway.yaml (Standard Pods >= 1.37.2-gke.1042000 Rapid:\n"
                "#   GCPAgentGateway + GCPAuthzPolicy + GCPExtensionBinding + GCPBackend + GCPEgressPolicy)\n"
                "# deployment/k8s/agent-substrate.yaml (ate.dev/v1alpha1 WorkerPool, Standard >= 1.37 only)\n"
                "# deployment/k8s/agent-sandbox.yaml (extensions.agents.x-k8s.io/v1beta1, warmPoolRef)"
            ),
            "console_guide": "MCP·외부 도구 호출이 있으면 Agent Gateway for GKE를, AI 생성 코드를 실행하면 Agent Substrate on GKE(대안: Agent Sandbox on GKE)를 적용하세요.",
        },
        {
            "id": "R5.1",
            "domain": "5. 관측성 & 프라이버시",
            "check_type": "RUNTIME_RECOMMENDED" if has_deployment else "ARCH_DECISION",
            "verification_scope": "RUNTIME_VERIFIED" if has_deployment else "ARCH_DECISION",
            "title": "OpenTelemetry GenAI 트레이싱 & 스팬 PII 차단 (`NO_CONTENT` · `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`)",
            "status": r51_status,
            "current_state": (
                f"OTEL_CAPTURE={otel_capture or '미설정'}, "
                f"ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS={adk_capture_spans or '미설정'}"
                if has_deployment
                else "Deployment 환경변수 미포함"
            ),
            "recommended_state": "Cloud Trace 연동 + OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false",
            "why_it_matters": (
                "`agents-cli` 기본 GKE 템플릿은 `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=true`로 설정되어 고객 PII가 포함된 프롬프트 원문이 Cloud Trace에 평문 기록될 수 있습니다. "
                "프로덕션에서는 `NO_CONTENT` + `false`로 전환하세요. (지연·오류율 알림 정책은 체크리스트 범위 밖이므로 gke-alert-configuration 플랫폼 Skill을 참고하세요.)"
            ),
            "how_to_fix_title": "OTel 프롬프트 미기록(NO_CONTENT) 적용",
            "gcloud_cmd": (
                "# 로컬 코드에 OTel NO_CONTENT 프라이버시 환경변수 적용\n"
                "uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-OB1"
            ),
            "terraform_snippet": (
                'env { name = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"; value = "NO_CONTENT" }\n'
                'env { name = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"; value = "false" }'
            ),
            "console_guide": "Deployment 환경변수에서 프롬프트 원문 캡처(`NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`)를 비활성화하세요.",
        },
    ]


    # GKE-S2 (conditional: AI-generated code execution) — emitted only when a sandbox/substrate pattern is detected
    if is_sandbox_pattern or is_substrate_pattern:
        if autopilot_substrate_conflict or allows_metadata_ip:
            s2_status = "RED"
        elif str(automount_sa).lower() == "false":
            s2_status = "GREEN"
        else:
            s2_status = "AMBER"
        checks.append({
            "id": "R4.4",
            "domain": "Security",
            "check_type": "RUNTIME_VERIFIED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "AI 생성 코드 격리 (Agent Substrate · Agent Sandbox)",
            "status": s2_status,
            "current_state": (
                f"실행 패턴: {exec_pattern_label}, automountServiceAccountToken={automount_sa}"
                + (" · 🔴 GKE Autopilot에서는 Agent Substrate를 사용할 수 없습니다 (GKE Standard >= 1.37 전용)" if autopilot_substrate_conflict else "")
                + (" · 🔴 샌드박스 Pod에서 Metadata 169.254.169.254 허용 감지" if allows_metadata_ip else "")
            ),
            "recommended_state": (
                "AI 생성 코드는 Agent Substrate on GKE 샌드박스에서 실행 (상시 실행 · 사이드카 · Arm · Autopilot이 필요하면 Agent Sandbox on GKE) + "
                "Metadata 169.254.169.254/32 · 169.254.169.252/32 차단 + automountServiceAccountToken: false"
            ),
            "why_it_matters": (
                "AI가 만든 코드는 신뢰할 수 없어, 에이전트 Pod 안에서 그대로 실행하면 자격증명 탈취나 노드 침해로 이어질 수 있습니다. "
                "샌드박스에서 커널·네트워크 격리되어 실행되고 메타데이터 서버에 접근할 수 없는지 확인하세요."
            ),
            "how_to_fix_title": "Agent Substrate(또는 Agent Sandbox) 매니페스트 적용",
            "gcloud_cmd": (
                "uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-S2 \\\n"
                "  --params '{\"gke_cluster_mode\":\"standard\",\"gke_execution_pattern\":\"agent_substrate\"}'"
            ),
            "terraform_snippet": "# deployment/k8s/agent-substrate.yaml (ate.dev/v1alpha1 WorkerPool) 또는 agent-sandbox.yaml",
            "console_guide": "샌드박스 Pod의 NetworkPolicy가 메타데이터 서버를 차단하고 서비스 계정 토큰을 마운트하지 않는지 확인하세요.",
        })

    # GKE-O2 (conditional: idle conversational agents) — the "idle" condition cannot be read from JSON, so the
    # check is emitted only when Agent Substrate is detected (suspend/resume capability in use).
    if is_substrate_pattern:
        checks.append({
            "id": "R7.1",
            "domain": "Optimize",
            "check_type": "RUNTIME_VERIFIED",
            "verification_scope": "RUNTIME_VERIFIED",
            "title": "에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox)",
            "status": "AMBER" if autopilot_substrate_conflict else "GREEN",
            "current_state": (
                "Agent Substrate(ate.dev) 리소스 감지 — 유휴 에이전트 스냅샷 일시정지 · 요청 시 복원 가능"
                + (" · 🟡 GKE Autopilot에서는 Agent Substrate를 사용할 수 없습니다 (GKE Standard >= 1.37 전용)" if autopilot_substrate_conflict else "")
            ),
            "recommended_state": "자주 일시정지·재개하는 상태 유지형 에이전트는 Agent Substrate on GKE(GKE Standard >= 1.37)에서 실행해 유휴 시 일시정지 · 요청 시 복원",
            "why_it_matters": (
                "대화형 에이전트는 대부분의 시간을 사용자 입력이나 도구 응답을 기다리며 보내므로, Pod를 계속 띄워 두면 "
                "유휴 에이전트가 CPU·메모리를 점유합니다."
            ),
            "how_to_fix_title": "Agent Substrate 매니페스트 적용 (GKE Standard)",
            "gcloud_cmd": "uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke --items GKE-O2 --conditions idle=true",
            "terraform_snippet": "# deployment/k8s/agent-substrate.yaml (ate.dev/v1alpha1 WorkerPool · ActorTemplate)",
            "console_guide": "GKE Standard 1.37 이상 클러스터에서 Agent Substrate 워커 풀이 동작하는지 확인하세요.",
        })

    verified_checks = [c for c in checks if c["status"] in ("GREEN", "AMBER", "RED")]
    green_cnt = sum(1 for c in checks if c["status"] == "GREEN")
    amber_cnt = sum(1 for c in checks if c["status"] == "AMBER")
    red_cnt = sum(1 for c in checks if c["status"] == "RED")
    review_cnt = sum(1 for c in checks if c["status"] == "REVIEW")

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
        "service_name": workload_name,
        "resource_id": f"{namespace_name}/{workload_name}",
        "runtime_type": "gke",
        "runtime_label": "GKE",
        "service_url": f"gke://{project_id}/{region}/{cluster_name}/{namespace_name}/{workload_name}",
        "created_by": dep_meta.get("labels", {}).get("created-by", "gke"),
        "service_account": ksa_name or "default",
        "inspected_at": now_str,
        "verdict": verdict,
        "total_count": len(checks),
        "green_count": green_cnt,
        "amber_count": amber_cnt,
        "red_count": red_cnt,
        "review_count": review_cnt,
        "verified_count": len(verified_checks),
        "detected_config": {
            "cluster_name": cluster_name,
            "cluster_mode": "autopilot" if is_autopilot is True else ("standard" if is_autopilot is False else "unknown"),
            "namespace": namespace_name,
            "replicas": replicas if replicas is not None else 1,
            "execution_pattern": exec_pattern,
            "cpu": cpu_req,
            "memory": mem_req,
            "location": loc_val or "global",
            "has_agent_gateway": has_agent_gateway,
            "has_zone_spread": has_zone_spread,
            "termination_grace_period_seconds": grace_sec,
            "has_pre_stop": has_pre_stop,
            "hardcoded_remote_urls": hardcoded_remote_urls,
        },
        "platform_guarantees": [],
        "combined_runtime_fix_cmd": (
            f"# ⚠️ 선택한 항목에 따라 UI에서 맞춤형 GKE 조치 명령어가 동적으로 생성됩니다.\n"
            f"uv run prod-ready apply <내_로컬_에이전트_폴더> --target gke"
        ),
        "stage1_skill_cmd": "uv run prod-ready scan <내_로컬_에이전트_코드_폴더> --target gke  # 체크리스트 v3 GKE 항목 (일반 플랫폼 BP는 플랫폼 Skill 참고)",
        "checks": checks,
    }


def _evaluate_cloud_run_service(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _apply_checklist_v3(_evaluate_cloud_run_service_legacy(*args, **kwargs))


def _evaluate_agent_runtime_engine(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _apply_checklist_v3(_evaluate_agent_runtime_engine_legacy(*args, **kwargs))


def _evaluate_gke_workload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _apply_checklist_v3(_evaluate_gke_workload_legacy(*args, **kwargs))
