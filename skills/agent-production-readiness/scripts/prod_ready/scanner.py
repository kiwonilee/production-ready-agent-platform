"""Static Scanner & Scoring Engine for the 7-Domain, 17-Item Production-Readiness Checklist (v2)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from prod_ready.checklist import (
    CHECKLIST_ITEMS,
    ChecklistItemSpec,
    CustomFieldSpec,
    RAGStatus,
    TargetType,
    resolve_item_ids,
)

ProjectMode = Literal["prototype", "agents_cli_single", "agents_cli_cicd"]


@dataclass
class ItemScanResult:
    id: str
    code_alias: str
    domain_id: str
    domain_name: str
    domain_weight: int
    title: str
    severity: str
    status: RAGStatus
    score: int
    managed_by_platform: bool
    evidence: str
    remediation: str
    target_files: list[str]
    why_it_matters: str
    verify_cmd: str
    cloud_run_desc: str
    agent_runtime_desc: str
    gke_desc: str
    custom_fields: list[CustomFieldSpec]


@dataclass
class DomainSummary:
    domain_id: str
    domain_name: str
    weight: int
    score: float
    green_count: int
    amber_count: int
    red_count: int


@dataclass
class ProjectScanReport:
    project_dir: str
    project_name: str
    project_mode: ProjectMode
    deployment_target: TargetType
    weighted_score: float
    verdict: str
    green_count: int
    amber_count: int
    red_count: int
    managed_native_count: int
    domains: list[DomainSummary]
    items: list[ItemScanResult]
    skipped_item_ids: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def detect_project_mode_and_target(
    project_dir: Path, target_override: str = "auto"
) -> tuple[ProjectMode, TargetType, str]:
    """Detects whether the project is a pure prototype or an agents-cli project, and its target."""
    tf_service = project_dir / "deployment" / "terraform" / "service.tf"
    tf_dev_service = project_dir / "deployment" / "terraform" / "dev" / "service.tf"
    staging_yaml = project_dir / ".cloudbuild" / "staging.yaml"

    if not tf_service.exists():
        mode: ProjectMode = "prototype"
    elif tf_dev_service.exists() or staging_yaml.exists():
        mode = "agents_cli_cicd"
    else:
        mode = "agents_cli_single"

    service_txt = _read_text(tf_service)
    if target_override in ("cloud_run", "agent_runtime"):
        target: TargetType = target_override  # type: ignore[assignment]
    elif "google_vertex_ai_reasoning_engine" in service_txt:
        target = "agent_runtime"
    else:
        target = "cloud_run"

    project_name = project_dir.name.replace("_", "-")
    pyproject_txt = _read_text(project_dir / "pyproject.toml")
    for line in pyproject_txt.splitlines():
        if line.strip().startswith("name") and "=" in line:
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                project_name = val.replace("_", "-")
                break

    return mode, target, project_name


def _collect_sizing_advisories(target: TargetType, service_tf: str) -> list[str]:
    """Produces non-scoring REVIEW advisories for sizing/scaling without ever forcing upsizing."""
    advisories: list[str] = []
    if target == "cloud_run":
        if service_tf:
            if "max_instance_request_concurrency = 80" in service_tf or (
                "google_cloud_run_v2_service" in service_tf
                and "max_instance_request_concurrency" not in service_tf
            ):
                advisories.append(
                    "[REVIEW · 사이징] Cloud Run 동시성이 기본 80으로 보입니다. "
                    "에이전트 스트리밍은 메모리 바운드이므로 tests/load_test/ 부하 테스트 후 적정 concurrency/memory를 직접 결정하세요."
                )
            else:
                advisories.append(
                    "[REVIEW · 사이징] CPU/Memory/Concurrency/min_instances는 임의 상향 패치하지 않고 사용자 설정을 보존합니다. "
                    "tests/load_test/(Locust) 부하 테스트로 P95 지연 및 OOM 여부를 관측한 뒤 직접 조정하세요."
                )
        else:
            advisories.append(
                "[REVIEW · 사이징] 신규 service.tf 생성 시 공식 agents-cli 기본 스펙(cpu=1, memory=4Gi, concurrency=8, min_instances=0)을 사용하며, "
                "부하 테스트 후 워크로드 특성에 맞게 직접 튜닝하는 것을 권장합니다."
            )
    else:
        advisories.append(
            "[REVIEW · 사이징] Agent Runtime의 cpu/memory/container_concurrency/min_instances는 임의 상향하지 않습니다. "
            "tests/load_test/ 부하 테스트 결과에 맞춰 deployment_spec을 직접 조정하세요."
        )
    advisories.append(
        "[참고 · 세션/메모리 옵션] D2는 운영 부담이 없는 Agent Platform Managed Session & Memory Bank를 기본 적용합니다. "
        "(폐쇄망 DB 강제·서브밀리초 캐시 등 특수 요건 시 Self-Hosted Cloud SQL / Memorystore Redis 자체 구축 옵션도 선택 가능합니다.)"
    )
    return advisories


def scan_project(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
) -> ProjectScanReport:
    """Evaluates active checklist items (all 17 by default, or tailored subset) against the local project."""
    root = Path(project_dir).resolve()
    mode, target, project_name = detect_project_mode_and_target(root, target_override)

    active_ids = set(resolve_item_ids(selected_items))
    skipped_item_ids = [spec.id for spec in CHECKLIST_ITEMS if spec.id not in active_ids]

    service_tf = _read_text(root / "deployment" / "terraform" / "service.tf")
    variables_tf = _read_text(root / "deployment" / "terraform" / "variables.tf")
    storage_tf = _read_text(root / "deployment" / "terraform" / "storage.tf")
    prod_ready_tf = _read_text(root / "deployment" / "terraform" / "production_ready.tf")
    alerts_tf = _read_text(root / "deployment" / "terraform" / "alerts.tf")
    budget_tf = _read_text(root / "deployment" / "terraform" / "budget.tf")
    dockerfile = _read_text(root / "Dockerfile")
    agent_py = _read_text(root / "app" / "agent.py")
    fast_api_py = _read_text(root / "app" / "fast_api_app.py")
    services_py = _read_text(root / "app" / "app_utils" / "services.py")
    model_armor_py = _read_text(root / "app" / "app_utils" / "model_armor_plugin.py")
    cost_guard_py = _read_text(root / "app" / "app_utils" / "cost_guard_plugin.py")
    staging_yaml = _read_text(root / ".cloudbuild" / "staging.yaml")
    eval_config = _read_text(root / "tests" / "eval" / "eval_config.yaml")
    eval_dataset = _read_text(root / "tests" / "eval" / "datasets" / "smoke_dataset.json") or _read_text(
        root / "tests" / "eval" / "golden_dataset.json"
    )
    canary_sh = _read_text(root / "scripts" / "canary_rollout.sh")

    has_tf = bool(service_tf)

    results: list[ItemScanResult] = []
    for spec in CHECKLIST_ITEMS:
        if spec.id not in active_ids:
            continue
        status, managed, evidence = _evaluate_item(
            spec=spec,
            target=target,
            has_tf=has_tf,
            service_tf=service_tf,
            variables_tf=variables_tf,
            storage_tf=storage_tf,
            prod_ready_tf=prod_ready_tf,
            alerts_tf=alerts_tf,
            budget_tf=budget_tf,
            dockerfile=dockerfile,
            agent_py=agent_py,
            fast_api_py=fast_api_py,
            services_py=services_py,
            model_armor_py=model_armor_py,
            cost_guard_py=cost_guard_py,
            staging_yaml=staging_yaml,
            eval_config=eval_config,
            eval_dataset=eval_dataset,
            canary_sh=canary_sh,
        )
        score = 100 if status == "GREEN" else (50 if status == "AMBER" else 0)
        remediation = (
            spec.agent_runtime_desc if target == "agent_runtime" else spec.cloud_run_desc
        )
        results.append(
            ItemScanResult(
                id=spec.id,
                code_alias=spec.code_alias,
                domain_id=spec.domain_id,
                domain_name=spec.domain_name,
                domain_weight=spec.domain_weight,
                title=spec.title,
                severity=spec.severity,
                status=status,
                score=score,
                managed_by_platform=managed,
                evidence=evidence,
                remediation=remediation,
                target_files=spec.target_files,
                why_it_matters=spec.why_it_matters,
                verify_cmd=spec.verify_cmd,
                cloud_run_desc=spec.cloud_run_desc,
                agent_runtime_desc=spec.agent_runtime_desc,
                gke_desc=spec.gke_desc,
                custom_fields=spec.custom_fields,
            )
        )

    # Compute Domain Summaries & Weighted Overall Score
    domain_map: dict[str, list[ItemScanResult]] = {}
    for r in results:
        domain_map.setdefault(r.domain_id, []).append(r)

    domain_summaries: list[DomainSummary] = []
    weighted_sum = 0.0
    total_weight = 0

    for d_id, d_items in domain_map.items():
        d_weight = d_items[0].domain_weight
        d_name = d_items[0].domain_name
        avg_score = sum(i.score for i in d_items) / len(d_items)
        domain_summaries.append(
            DomainSummary(
                domain_id=d_id,
                domain_name=d_name,
                weight=d_weight,
                score=round(avg_score, 1),
                green_count=sum(1 for i in d_items if i.status == "GREEN"),
                amber_count=sum(1 for i in d_items if i.status == "AMBER"),
                red_count=sum(1 for i in d_items if i.status == "RED"),
            )
        )
        weighted_sum += avg_score * d_weight
        total_weight += d_weight

    overall_score = round(weighted_sum / total_weight, 1) if total_weight else 0.0
    green_count = sum(1 for r in results if r.status == "GREEN")
    amber_count = sum(1 for r in results if r.status == "AMBER")
    red_count = sum(1 for r in results if r.status == "RED")
    managed_count = sum(1 for r in results if r.managed_by_platform)

    if overall_score >= 90.0 and red_count == 0:
        verdict = "🟢 Production Ready"
    elif overall_score >= 70.0 and red_count == 0:
        verdict = "🟡 Ready with Follow-ups"
    else:
        verdict = "🔴 Not Production Ready"

    return ProjectScanReport(
        project_dir=str(root),
        project_name=project_name,
        project_mode=mode,
        deployment_target=target,
        weighted_score=overall_score,
        verdict=verdict,
        green_count=green_count,
        amber_count=amber_count,
        red_count=red_count,
        managed_native_count=managed_count,
        domains=domain_summaries,
        items=results,
        skipped_item_ids=skipped_item_ids,
        advisories=_collect_sizing_advisories(target, service_tf),
    )


def _evaluate_item(
    *,
    spec: ChecklistItemSpec,
    target: TargetType,
    has_tf: bool,
    service_tf: str,
    variables_tf: str,
    storage_tf: str,
    prod_ready_tf: str,
    alerts_tf: str,
    budget_tf: str,
    dockerfile: str,
    agent_py: str,
    fast_api_py: str,
    services_py: str,
    model_armor_py: str,
    cost_guard_py: str,
    staging_yaml: str,
    eval_config: str,
    eval_dataset: str,
    canary_sh: str,
) -> tuple[RAGStatus, bool, str]:
    cid = spec.id

    # Agent Runtime natively managed items (except C7.2 which still verifies ignore_changes)
    if target == "agent_runtime" and spec.agent_runtime_mode == "MANAGED_NATIVE":
        if cid == "C7.2":
            if has_tf and "google_vertex_ai_reasoning_engine" in service_tf and "ignore_changes" in service_tf:
                return (
                    "GREEN",
                    True,
                    "Agent Runtime: Cloud Run 트래픽 분할 N/A · service.tf lifecycle.ignore_changes 보존 확인됨",
                )
            return (
                "AMBER",
                False,
                "service.tf에 lifecycle.ignore_changes (container_spec/source_code_spec/deployment_spec) 누락",
            )
        if has_tf and "google_vertex_ai_reasoning_engine" in service_tf:
            return (
                "GREEN",
                True,
                "Managed natively by Agent Runtime (google_vertex_ai_reasoning_engine.app)",
            )
        return (
            "RED",
            False,
            "deployment/terraform/service.tf (google_vertex_ai_reasoning_engine.app) 미생성",
        )

    # C1.1: IaC resource primitive mapping (Cloud Run)
    if cid == "C1.1":
        if has_tf and "google_cloud_run_v2_service" in service_tf:
            return ("GREEN", False, "deployment/terraform/service.tf: google_cloud_run_v2_service.app 확인됨")
        return ("RED", False, "deployment/terraform/service.tf 미생성 (프로토타입 상태)")

    # C1.2: Health probes & Startup CPU Boost (Cloud Run; NO forced resource upsizing!)
    if cid == "C1.2":
        if not has_tf:
            return ("AMBER", False, "deployment/terraform/service.tf 미생성")
        has_probes = "startup_probe" in service_tf and "liveness_probe" in service_tf
        has_boost = bool(re.search(r"startup_cpu_boost\s*=\s*true", service_tf))
        if has_probes and has_boost:
            return ("GREEN", False, "service.tf: startup_probe · liveness_probe · startup_cpu_boost=true 구성됨")
        if has_probes:
            return ("AMBER", False, "service.tf: 헬스 프로브는 있으나 startup_cpu_boost=true 누락")
        return ("AMBER", False, "service.tf: 컨테이너 startup_probe / liveness_probe 미구성")

    # C2.1: Persistent Multi-Turn Session Store (Default: Agent Platform Managed Sessions)
    if cid == "C2.1":
        has_managed_code = "VertexAiSessionService" in services_py
        has_managed_tf = (
            "GOOGLE_CLOUD_AGENT_ENGINE_ID" in service_tf
            or "ADK_SESSION_SERVICE_URI" in service_tf
        )
        # Also accept legitimately hardened self-hosted Cloud SQL if already configured by user
        has_hardened_self_sql = (
            "google_sql_database_instance" in service_tf
            and 'availability_type = "REGIONAL"' in service_tf
            and "point_in_time_recovery_enabled = true" in service_tf
            and "deletion_protection = true" in service_tf
            and "postgresql+asyncpg" in services_py
        )
        if has_managed_code and has_managed_tf:
            return (
                "GREEN",
                False,
                "Agent Platform Managed Session Service (VertexAiSessionService + GOOGLE_CLOUD_AGENT_ENGINE_ID) 연동 완료",
            )
        if has_hardened_self_sql:
            return (
                "GREEN",
                False,
                "Self-Hosted Cloud SQL REGIONAL HA + PITR 감지됨 (운영 부담이 적은 Managed Sessions 전환도 옵션으로 가능)",
            )
        if has_managed_code or "postgresql+asyncpg" in services_py:
            return (
                "AMBER",
                False,
                "services.py 또는 service.tf의 Managed Session (GOOGLE_CLOUD_AGENT_ENGINE_ID) 설정이 일부 누락됨",
            )
        return (
            "RED",
            False,
            "InMemorySessionService 사용 중 (스케일아웃·재시작 시 대화 증발 — Agent Platform Managed Sessions 연동 필요)",
        )

    # C2.2: Cross-Session Memory Bank (VertexAiMemoryBankService)
    if cid == "C2.2":
        has_mem_svc = "VertexAiMemoryBankService" in services_py and "MEMORY_SERVICE_URI" in services_py
        has_mem_hook = "add_session_to_memory" in agent_py or "auto_save_session_to_memory" in agent_py
        if has_mem_svc and has_mem_hook:
            return (
                "GREEN",
                False,
                "Agent Platform Memory Bank (VertexAiMemoryBankService + after_agent_callback 자동 저장) 연동 완료",
            )
        if has_mem_svc:
            return (
                "AMBER",
                False,
                "services.py에 VertexAiMemoryBankService는 있으나 agent.py의 자동 저장 콜백(after_agent_callback) 누락",
            )
        return (
            "RED",
            False,
            "Agent Platform Memory Bank (VertexAiMemoryBankService) 미연동 (세션 간 장기 기억 불가)",
        )

    # C2.3: GCS artifact / logs bucket durability
    if cid == "C2.3":
        if "versioning" in storage_tf and "soft_delete_policy" in storage_tf:
            return ("GREEN", False, "storage.tf: GCS 버킷 Object Versioning 및 7일 Soft Delete 활성화됨")
        if storage_tf:
            return ("AMBER", False, "storage.tf: 버킷 Versioning 또는 Soft Delete 정책 미적용")
        return ("AMBER", False, "deployment/terraform/storage.tf 미구성")

    # C3.1: Private network egress + Private Google Access (merges old C3.3)
    if cid == "C3.1":
        has_pga = bool(re.search(r"private_ip_google_access\s*=\s*true", prod_ready_tf))
        if target == "cloud_run":
            if "vpc_access" in service_tf and "google_compute_subnetwork" in prod_ready_tf and has_pga:
                return (
                    "GREEN",
                    False,
                    "Cloud Run Direct VPC Egress (PRIVATE_RANGES_ONLY) + Private Google Access 서브넷 구성 완료",
                )
            return (
                "RED",
                False,
                "service.tf vpc_access 또는 Private Google Access 전용 서브넷 미구성 (사내망 DB/API 접근 불가)",
            )
        else:
            has_na = "google_compute_network_attachment" in prod_ready_tf
            has_wired = "psc_interface_config" in service_tf
            if has_na and has_pga and has_wired:
                return (
                    "GREEN",
                    False,
                    "Agent Runtime PSC Interface (Network Attachment + service.tf psc_interface_config + PGA) 연결 완료",
                )
            if has_na and not has_wired:
                return (
                    "AMBER",
                    False,
                    "PSC Network Attachment는 있으나 service.tf의 psc_interface_config에 연결되지 않음",
                )
            return ("RED", False, "Agent Runtime 사설망 Egress (PSC Interface Network Attachment) 미구성")

    # C3.2: Zero-Trust Ingress & Edge WAF / Internal IAP (Cloud Run)
    if cid == "C3.2":
        is_lb_locked = (
            '"INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"' in service_tf
            and "default_uri_disabled" in service_tf
            and "google_compute_security_policy" in prod_ready_tf
        )
        is_internal_locked = '"INGRESS_TRAFFIC_INTERNAL_ONLY"' in service_tf
        if is_lb_locked:
            return (
                "GREEN",
                False,
                "service.tf: INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER + default_uri_disabled + Cloud Armor WAF/NEG 구성 완료",
            )
        if is_internal_locked:
            return (
                "GREEN",
                False,
                "service.tf: INGRESS_TRAFFIC_INTERNAL_ONLY (사내 전용 인바운드 격리) 적용됨",
            )
        if '"INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"' in service_tf:
            return ("AMBER", False, "Ingress는 제한되었으나 Cloud Armor WAF 또는 default_uri_disabled 누락")
        return ("RED", False, 'service.tf: ingress = "INGRESS_TRAFFIC_ALL" (공용 인터넷에 *.run.app 직접 노출)')

    # C4.1: Least-privilege SA, deletion protection & Secret Manager version pinning
    if cid == "C4.1":
        has_least_priv = bool(
            variables_tf
            and "roles/storage.admin" not in variables_tf
            and "roles/storage.objectUser" in variables_tf
        )
        if target == "cloud_run":
            has_pinned_sec = not re.search(r'version\s*=\s*"latest"', service_tf) and has_tf
            has_del = bool(re.search(r"deletion_protection\s*=\s*true", service_tf))
            if has_least_priv and has_pinned_sec and has_del:
                return (
                    "GREEN",
                    False,
                    "Cloud Run: roles/storage.objectUser 최소 권한 · deletion_protection=true · Secret 버전 고정 완료",
                )
            return (
                "RED",
                False,
                "variables.tf 과도한 권한(roles/storage.admin), deletion_protection=false 또는 Secret version='latest' 사용 중",
            )
        else:
            has_no_sql_bloat = "roles/cloudsql.client" not in variables_tf
            if has_least_priv and has_no_sql_bloat:
                return (
                    "GREEN",
                    False,
                    "Agent Runtime: roles/storage.objectUser 최소 권한 적용 (불필요한 Cloud SQL 권한 없음)",
                )
            if has_least_priv:
                return (
                    "AMBER",
                    False,
                    "storage.objectUser는 적용되었으나 Agent Runtime에 불필요한 roles/cloudsql.client가 남아 있음",
                )
            return ("RED", False, "variables.tf: 과도한 권한(roles/storage.admin) 부여 상태")

    # C4.2: Model Armor API input/output/PII & Tool SSRF/IDOR guardrails (merges old C4.4)
    if cid == "C4.2":
        has_plugin = bool(model_armor_py and "ModelArmorGuardrailPlugin" in agent_py)
        has_real_api = "modelarmor" in model_armor_py.lower() and "sanitize_user_prompt" in model_armor_py
        has_tool_guard = "before_tool_callback" in model_armor_py and "169\\.254\\." in model_armor_py
        if has_plugin and has_real_api and has_tool_guard:
            return (
                "GREEN",
                False,
                "ModelArmorGuardrailPlugin (Google Cloud Model Armor API + 로컬 PII 폴백 + SSRF/IDOR before_tool_callback) 장착 완료",
            )
        if has_plugin:
            return (
                "AMBER",
                False,
                "가드레일 플러그인은 등록되었으나 실제 Model Armor API 연동 또는 도구 SSRF/IDOR 검증 보강 필요",
            )
        return ("RED", False, "Model Armor 입출력/PII 가드레일 및 도구 SSRF/IDOR 차단 플러그인 미장착")

    # C4.3: Container non-root hardening & Gen2 sandbox
    if cid == "C4.3":
        has_non_root = "USER 10001" in dockerfile or "USER appuser" in dockerfile
        if target == "cloud_run":
            has_gen2 = "EXECUTION_ENVIRONMENT_GEN2" in service_tf
            if has_non_root and has_gen2:
                return ("GREEN", False, "Dockerfile Non-root(UID 10001) 및 Cloud Run EXECUTION_ENVIRONMENT_GEN2 적용됨")
            if has_non_root:
                return ("AMBER", False, "Dockerfile Non-root는 적용되었으나 EXECUTION_ENVIRONMENT_GEN2 미지정")
            return ("RED", False, "Dockerfile이 root(UID 0) 계정으로 실행됨")
        else:
            if has_non_root:
                return ("GREEN", False, "Agent Runtime: Dockerfile Non-root(UID 10001) 하드닝 완료")
            return ("RED", False, "Dockerfile이 root(UID 0) 계정으로 실행됨")

    # C5.1: OpenTelemetry GenAI distributed tracing (Cloud Run)
    if cid == "C5.1":
        if "OTEL_SEMCONV_STABILITY_OPT_IN" in service_tf and "otel_to_cloud" in fast_api_py:
            return ("GREEN", False, "service.tf 및 fast_api_app.py: OpenTelemetry GenAI 분산 트레이싱 활성화됨")
        if "otel_to_cloud" in fast_api_py:
            return ("AMBER", False, "fast_api_app.py에 otel_to_cloud는 있으나 Terraform OTel 환경변수 누락")
        return ("RED", False, "OpenTelemetry 분산 트레이싱 미구성")

    # C5.2: Trace Privacy (NO_CONTENT mandatory) & Optional BigQuery Analytics
    if cid == "C5.2":
        has_no_content = bool(re.search(r'value\s*=\s*"NO_CONTENT"', service_tf))
        has_adk_span_off = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS" in service_tf and bool(
            re.search(r'value\s*=\s*"false"', service_tf)
        )
        has_bq = "BigQueryAgentAnalyticsPlugin" in agent_py
        if has_no_content and has_adk_span_off:
            bq_note = " + 선택적 BigQueryAgentAnalyticsPlugin 활성" if has_bq else " (선택적 BQ Analytics는 미사용)"
            return ("GREEN", False, f"NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 프라이버시 가드 완료{bq_note}")
        if has_no_content:
            return (
                "AMBER",
                False,
                "OTEL NO_CONTENT는 설정되었으나 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 누락",
            )
        return ("RED", False, "Cloud Trace 스팬 본문 노출 차단(NO_CONTENT) 미구성")

    # C5.3: Target-Specific SRE Golden Signal Alert Policies (alerts.tf)
    if cid == "C5.3":
        if not alerts_tf or "google_monitoring_alert_policy" not in alerts_tf:
            return ("RED", False, "deployment/terraform/alerts.tf 없음 (프로덕션 장애/지연/공격 알람 전무)")
        if target == "agent_runtime":
            if "ReasoningEngine" in alerts_tf and "cloud_run_revision" not in alerts_tf:
                return (
                    "GREEN",
                    False,
                    "alerts.tf: Agent Runtime (ReasoningEngine) 전용 메트릭 및 Guardrail 알럿 구성 완료",
                )
            return (
                "AMBER",
                False,
                "alerts.tf가 Agent Runtime이 아닌 cloud_run_revision 메트릭을 참조 중 (타겟 불일치)",
            )
        else:
            has_ratio = "condition_prometheus_query_language" in alerts_tf
            if "cloud_run_revision" in alerts_tf and has_ratio:
                return (
                    "GREEN",
                    False,
                    "alerts.tf: Cloud Run P95 지연 · PromQL 5xx 에러율(>2%) · Guardrail 알럿 구성 완료",
                )
            return (
                "AMBER",
                False,
                "alerts.tf의 5xx 에러 알람이 비율(PromQL)이 아닌 절대 속도로 설정됨 — 업그레이드 필요",
            )

    # C6.1: Per-turn Loop Cap (with invocation reset + max_output_tokens) & Monthly Budget
    if cid == "C6.1":
        has_budget = bool(budget_tf and "google_billing_budget" in budget_tf)
        has_cost_plugin = bool(
            cost_guard_py
            and "CostTokenGuardrailPlugin" in agent_py
            and "invocation_id" in cost_guard_py
            and "max_output_tokens" in cost_guard_py
        )
        if has_budget and has_cost_plugin:
            return (
                "GREEN",
                False,
                "CostTokenGuardrailPlugin (invocation별 턴 리셋 + max_output_tokens 캡) + 선택적 budget.tf 완료",
            )
        if has_budget or (cost_guard_py and "CostTokenGuardrailPlugin" in agent_py):
            return (
                "AMBER",
                False,
                "예산 알람 또는 턴별 리셋/토큰 캡 로직 중 일부가 누락됨",
            )
        return ("RED", False, "무한 루프 방지 가드 및 월간 예산 알람(budget.tf) 전무")

    # C6.2: LLM API 429 DSQ quota resilience & exponential backoff
    if cid == "C6.2":
        has_retry = "attempts=5" in agent_py
        has_loc = "GOOGLE_CLOUD_LOCATION" in service_tf
        if has_retry and has_loc:
            return (
                "GREEN",
                False,
                "agent.py: HttpRetryOptions(attempts=5, 지수 백오프) 및 service.tf GOOGLE_CLOUD_LOCATION 구성 완료",
            )
        if "HttpRetryOptions" in agent_py:
            return ("AMBER", False, "기본 재시도(attempts=3)만 설정됨 — attempts=5 지수 백오프 강화 권장")
        return ("RED", False, "LLM API 429 재시도 옵션 미설정")

    # C7.1: Automated Eval Quality Gate in CI/CD
    if cid == "C7.1":
        has_eval_cmd = "agents-cli eval run" in staging_yaml or "google-agents-cli eval run" in staging_yaml
        has_bad_flags = "--eval-dataset" in staging_yaml or "--threshold" in staging_yaml
        if has_eval_cmd and not has_bad_flags and (eval_config or eval_dataset):
            return (
                "GREEN",
                False,
                ".cloudbuild/staging.yaml: 공식 agents-cli eval run 품질 게이트 및 평가 데이터셋/설정 확인됨",
            )
        if has_eval_cmd and has_bad_flags:
            return (
                "AMBER",
                False,
                ".cloudbuild/staging.yaml에 미지원 플래그(--eval-dataset/--threshold) 사용 — 공식 --dataset/--metrics 수정 필요",
            )
        if staging_yaml:
            return ("RED", False, ".cloudbuild/staging.yaml에 agents-cli eval run 품질 게이트 누락")
        return ("RED", False, ".cloudbuild/staging.yaml CI/CD 파이프라인 및 Eval 게이트 미구성")

    # C7.2: Cloud Run Canary Rollout & Previous-Revision Rollback
    if cid == "C7.2":
        has_ignore = "ignore_changes" in service_tf
        has_safe_rollback = bool(canary_sh and "--to-latest" not in canary_sh and "PREVIOUS_REVISION" in canary_sh)
        if has_ignore and has_safe_rollback:
            return (
                "GREEN",
                False,
                "Cloud Run: service.tf ignore_changes 및 이전 안정 리비전 롤백을 지원하는 canary_rollout.sh 구비됨",
            )
        if has_ignore and canary_sh:
            return (
                "AMBER",
                False,
                "canary_rollout.sh의 rollback이 --to-latest를 사용 중 — 이전 안정 리비전 복원으로 수정 필요",
            )
        if has_ignore:
            return ("AMBER", False, "service.tf ignore_changes는 있으나 Cloud Run 카나리/롤백 스크립트 미구비")
        return ("AMBER", False, "무중단 카나리 배포 및 롤백 자동화 미구성")

    return ("AMBER", False, "점검 필요")
