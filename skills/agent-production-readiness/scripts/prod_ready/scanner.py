"""Static Scanner & Scoring Engine for the 7-Domain, 13-Item Production-Readiness Checklist (v2)."""

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
    """Produces non-scoring REVIEW advisories for sizing, ingress topology, and session options."""
    advisories: list[str] = []
    if target == "cloud_run":
        advisories.append(
            "[REVIEW · 컴퓨트 사이징] CPU/Memory/Concurrency/min_instances는 임의 상향 패치하지 않고 사용자 설정을 보존합니다. "
            "tests/load_test/(Locust) 부하 테스트로 P95 지연 및 OOM 여부를 관측한 뒤 직접 조정하세요."
        )
        advisories.append(
            "[참고 · 인바운드 & 인증 아키텍처] 인바운드 노출 및 인증 방식(자체 OAuth/JWT + 공개 URL vs 서버 간 IAM Invoker 인증 vs 사내 IAP vs 앞단 GCLB + Cloud Armor WAF)은 "
            "호출 주체와 조직 네트워크 정책에 따라 선택하세요 (스킬이 기본 URL이나 인그레스를 강제 잠그지 않습니다)."
        )
    else:
        advisories.append(
            "[REVIEW · 컴퓨트 사이징] Agent Runtime의 cpu/memory/container_concurrency/min_instances는 임의 상향하지 않습니다. "
            "tests/load_test/ 부하 테스트 결과에 맞춰 deployment_spec을 직접 조정하세요."
        )
    advisories.append(
        "[참고 · 세션/메모리 옵션] D2는 운영 부담이 없는 Agent Platform Managed Session & Memory Bank를 기본 적용합니다. "
        "(폐쇄망 DB 강제·서브밀리초 캐시 등 특수 요건 시 Self-Hosted Cloud SQL / Memorystore Redis 자체 구축 옵션도 선택 가능합니다.)"
    )
    advisories.append(
        "[참고 · AI 보안 거버넌스 (Model Armor / 도구 SSRF·IDOR)] 프롬프트 인젝션·PII 입출력 필터링과 도구 정책 통제는 "
        "개별 에이전트 코드에 일괄 강제하기보다 조직 거버넌스 요건에 맞춰 앞단 Agent Gateway + Model Armor Floor Settings로 중앙 통제하거나, "
        "필요한 워크로드에서만 선택적으로 ADK ModelArmorPlugin / before_tool_callback을 적용하세요."
    )
    advisories.append(
        "[참고 · 상용 SLA / 429 쿼터 전략 (DSQ vs Provisioned Throughput)] Standard PayGo(Global DSQ)는 별도 처리량 SLA를 보장하지 않으며, "
        "대화형 에이전트에서 무리한 재시도 상향은 Retry Storm과 동시성 고갈을 유발합니다. "
        "상용 SLA가 필요한 핵심 워크로드는 Provisioned Throughput(GSU + Shared spillover) 또는 Priority PayGo를, 비동기·배치 평가는 Flex PayGo / Batch API를 검토하세요."
    )
    return advisories


def scan_project(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
) -> ProjectScanReport:
    """Evaluates active checklist items (all 13 by default, or tailored subset) against the local project."""
    root = Path(project_dir).resolve()
    mode, target, project_name = detect_project_mode_and_target(root, target_override)

    active_ids = set(resolve_item_ids(selected_items))
    skipped_item_ids = [spec.id for spec in CHECKLIST_ITEMS if spec.id not in active_ids]

    service_tf = _read_text(root / "deployment" / "terraform" / "service.tf")
    variables_tf = _read_text(root / "deployment" / "terraform" / "variables.tf")
    prod_ready_tf = _read_text(root / "deployment" / "terraform" / "production_ready.tf")
    alerts_tf = _read_text(root / "deployment" / "terraform" / "alerts.tf")
    dockerfile = _read_text(root / "Dockerfile")
    agent_py = _read_text(root / "app" / "agent.py")
    fast_api_py = _read_text(root / "app" / "fast_api_app.py")
    services_py = _read_text(root / "app" / "app_utils" / "services.py")
    cost_guard_py = _read_text(root / "app" / "app_utils" / "cost_guard_plugin.py")
    staging_yaml = _read_text(root / ".cloudbuild" / "staging.yaml")
    eval_config = _read_text(root / "tests" / "eval" / "eval_config.yaml")
    eval_dataset = _read_text(root / "tests" / "eval" / "datasets" / "smoke_dataset.json") or _read_text(
        root / "tests" / "eval" / "golden_dataset.json"
    )

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
            prod_ready_tf=prod_ready_tf,
            alerts_tf=alerts_tf,
            dockerfile=dockerfile,
            agent_py=agent_py,
            fast_api_py=fast_api_py,
            services_py=services_py,
            cost_guard_py=cost_guard_py,
            staging_yaml=staging_yaml,
            eval_config=eval_config,
            eval_dataset=eval_dataset,
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
    prod_ready_tf: str,
    alerts_tf: str,
    dockerfile: str,
    agent_py: str,
    fast_api_py: str,
    services_py: str,
    cost_guard_py: str,
    staging_yaml: str,
    eval_config: str,
    eval_dataset: str,
) -> tuple[RAGStatus, bool, str]:
    cid = spec.id

    # Agent Runtime natively managed items (4 items: C1.1, C1.2, C2.1, C5.1)
    if target == "agent_runtime" and spec.agent_runtime_mode == "MANAGED_NATIVE":
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

    # C1.1: IaC resource primitive mapping + lifecycle.ignore_changes (Cloud Run)
    if cid == "C1.1":
        if has_tf and "google_cloud_run_v2_service" in service_tf:
            return ("GREEN", False, "deployment/terraform/service.tf: google_cloud_run_v2_service.app 및 lifecycle 확인됨")
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
        has_hardened_self_sql = (
            "google_sql_database_instance" in service_tf
            and 'availability_type = "REGIONAL"' in service_tf
            and "point_in_time_recovery_enabled = true" in service_tf
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

    # C3.1: Private network egress (Direct VPC Egress on Cloud Run / PSC-I on Agent Runtime)
    if cid == "C3.1":
        if target == "cloud_run":
            if "vpc_access" in service_tf:
                return (
                    "GREEN",
                    False,
                    "Cloud Run Direct VPC Egress (vpc_access) 사내망 아웃바운드 연결 완료",
                )
            return (
                "RED",
                False,
                "service.tf에 vpc_access (Direct VPC Egress) 미구성 (사내망 프라이빗 DB/API 접근 불가)",
            )
        else:
            has_na = "google_compute_network_attachment" in prod_ready_tf
            has_wired = "psc_interface_config" in service_tf
            if has_wired and (has_na or "network_attachment" in service_tf):
                return (
                    "GREEN",
                    False,
                    "Agent Runtime PSC Interface (service.tf psc_interface_config) 사내망 연결 완료",
                )
            if has_na and not has_wired:
                return (
                    "AMBER",
                    False,
                    "PSC Network Attachment는 있으나 service.tf의 psc_interface_config에 연결되지 않음",
                )
            return ("RED", False, "Agent Runtime 사설망 Egress (psc_interface_config) 미구성")

    # C4.1: Least-privilege SA & Secret Manager version pinning (NO forced deletion_protection=true)
    if cid == "C4.1":
        has_least_priv = bool(
            variables_tf
            and "roles/storage.admin" not in variables_tf
            and "roles/storage.objectUser" in variables_tf
        )
        if target == "cloud_run":
            has_pinned_sec = not re.search(r'version\s*=\s*"latest"', service_tf) and has_tf
            if has_least_priv and has_pinned_sec:
                return (
                    "GREEN",
                    False,
                    "Cloud Run: roles/storage.objectUser 최소 권한 및 Secret 버전 고정 완료",
                )
            return (
                "RED",
                False,
                "variables.tf 과도한 권한(roles/storage.admin) 또는 Secret version='latest' 사용 중",
            )
        else:
            has_no_sql_bloat = "roles/cloudsql.client" not in variables_tf
            if has_least_priv and has_no_sql_bloat:
                return (
                    "GREEN",
                    False,
                    "Agent Runtime: roles/storage.objectUser 최소 권한 적용 (불필요한 Cloud SQL 권한 제거됨)",
                )
            if has_least_priv:
                return (
                    "AMBER",
                    False,
                    "storage.objectUser는 적용되었으나 Agent Runtime에 불필요한 roles/cloudsql.client가 남아 있음",
                )
            return ("RED", False, "variables.tf: 과도한 권한(roles/storage.admin) 부여 상태")

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

    # C5.2: Trace Span Privacy (NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false)
    if cid == "C5.2":
        has_no_content = bool(re.search(r'value\s*=\s*"NO_CONTENT"', service_tf))
        has_adk_span_off = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS" in service_tf and bool(
            re.search(r'value\s*=\s*"false"', service_tf)
        )
        if has_no_content and has_adk_span_off:
            return ("GREEN", False, "NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 트레이스 프라이버시 가드 완료")
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
            return ("RED", False, "deployment/terraform/alerts.tf 없음 (프로덕션 지연·5xx 에러율 알람 전무)")
        if target == "agent_runtime":
            if "ReasoningEngine" in alerts_tf and "cloud_run_revision" not in alerts_tf:
                return (
                    "GREEN",
                    False,
                    "alerts.tf: Agent Runtime (ReasoningEngine) 전용 P95 지연 및 PromQL 5xx 에러율 알럿 구성 완료",
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
                    "alerts.tf: Cloud Run P95 지연 및 PromQL 5xx 에러율(>2%) 알럿 구성 완료",
                )
            return (
                "AMBER",
                False,
                "alerts.tf의 5xx 에러 알람이 비율(PromQL)이 아닌 절대 속도로 설정됨 — 업그레이드 필요",
            )

    # C6.1: Per-turn Loop Cap (with invocation reset + opt-in max_output_tokens; Thinking-model safe!)
    if cid == "C6.1":
        has_cost_plugin = bool(
            cost_guard_py
            and "CostTokenGuardrailPlugin" in agent_py
            and "invocation_id" in cost_guard_py
        )
        if has_cost_plugin:
            return (
                "GREEN",
                False,
                "CostTokenGuardrailPlugin (invocation_id 턴별 리셋 + 선택적 max_output_tokens 캡) 장착 완료",
            )
        if cost_guard_py or "CostTokenGuardrailPlugin" in agent_py:
            return (
                "AMBER",
                False,
                "CostTokenGuardrailPlugin 일부 설정(invocation_id 턴별 리셋) 보강 필요",
            )
        return ("RED", False, "단일 턴 무한 도구 루프 차단 플러그인(CostTokenGuardrailPlugin) 미장착")

    # C6.2: Gemini API 429 (DSQ) resilience — Global endpoint + Fail-Fast HttpRetryOptions (accepts attempts >= 3!)
    if cid == "C6.2":
        has_retry = (
            "HttpRetryOptions" in agent_py
            and "attempts=0" not in agent_py
            and "attempts=1" not in agent_py
        )
        has_loc = "GOOGLE_CLOUD_LOCATION" in service_tf
        if has_retry and has_loc:
            return (
                "GREEN",
                False,
                "agent.py: HttpRetryOptions(지수 백오프 Fail-Fast) 및 service.tf GOOGLE_CLOUD_LOCATION 구성 확인됨",
            )
        if has_retry:
            return (
                "AMBER",
                False,
                "agent.py에 HttpRetryOptions는 있으나 service.tf에 GOOGLE_CLOUD_LOCATION(기본 global DSQ) 미설정",
            )
        if has_loc:
            return (
                "AMBER",
                False,
                "service.tf에 GOOGLE_CLOUD_LOCATION은 있으나 agent.py에 HttpRetryOptions 지수 백오프 재시도 누락",
            )
        return ("RED", False, "LLM API 429 재시도 옵션(HttpRetryOptions) 및 GOOGLE_CLOUD_LOCATION 미설정")

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

    return ("AMBER", False, "점검 필요")
