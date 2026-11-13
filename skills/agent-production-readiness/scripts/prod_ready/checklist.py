"""Canonical 7-Domain, 13-Item Production-Readiness Checklist, Guide & Customization Schema (v2).

Defines the 7 Domains, 13 core "True No-Regret" Checklist Items (C1.1-C7.1), customizable parameters,
and explicit runtime mappings separating Cloud Run (`cloud_run`) and Agent Runtime (`agent_runtime`).

Key v2 Principles:
1. No unsolicited CPU/Memory/Concurrency/min_instances upsizing — sizing belongs to load testing (`tests/load_test/`).
2. No over-reaching org/infra/governance mutations (no forced GCLB/Cloud Armor WAF, no forced Billing Account `budget.tf`,
   no forced GCS log bucket versioning, no forced `deletion_protection=true`, no forced Model Armor / tool SSRF plugin —
   centralized governance belongs to Agent Gateway / Model Armor Floor Settings advisory).
3. Thinking-model safe & Retry-Storm safe defaults: `max_output_tokens = 0` (opt-in so Gemini 2.5/3.x thoughts aren't
   truncated), and `C6.2` preserves `agents-cli`'s Fail-Fast `attempts=3` instead of forcing `attempts=5`.
4. Domain 2 defaults to Agent Platform Managed Session Service (`VertexAiSessionService`) and
   Memory Bank (`VertexAiMemoryBankService`); Self-Hosted Cloud SQL / Redis is noted only as an optional alternative.
5. Strict runtime separation between Cloud Run and Agent Runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TargetType = Literal["cloud_run", "agent_runtime"]
RAGStatus = Literal["GREEN", "AMBER", "RED"]
TierType = Literal["TIER_1_AUTO", "TIER_2_TEMPLATE", "MANAGED_NATIVE"]


@dataclass(frozen=True)
class CustomFieldSpec:
    key: str
    label: str
    default: str
    options: list[str] = field(default_factory=list)


@dataclass
class CustomParams:
    workload_type: str = "service"
    agent_engine_id: str = ""
    vpc_network_name: str = "default"
    vpc_subnet_name: str = "default"
    subnet_cidr: str = "10.128.64.0/28"
    egress_mode: str = "PRIVATE_RANGES_ONLY"
    latency_p95_ms: int = 15000
    model_location: str = "global"
    max_llm_calls_per_turn: int = 10
    max_output_tokens: int = 0
    eval_metrics: str = "final_response_quality,safety"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CustomParams:
        if not data:
            return cls()
        instance = cls()
        for k, v in data.items():
            if k not in cls.__dataclass_fields__:
                continue
            current = getattr(instance, k)
            if isinstance(current, bool):
                if isinstance(v, bool):
                    setattr(instance, k, v)
                elif isinstance(v, str):
                    setattr(instance, k, v.strip().lower() in ("true", "1", "yes", "on"))
                else:
                    setattr(instance, k, bool(v))
            else:
                field_type = type(current)
                try:
                    setattr(instance, k, field_type(v))
                except Exception:
                    pass
        return instance

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChecklistItemSpec:
    id: str
    code_alias: str
    domain_id: str
    domain_name: str
    domain_weight: int
    title: str
    severity: Literal["Critical", "Standard"]
    before_summary: str
    after_criteria: str
    why_it_matters: str
    verify_cmd: str
    agent_runtime_mode: TierType
    agent_runtime_desc: str
    cloud_run_mode: TierType
    cloud_run_desc: str
    gke_desc: str
    target_files: list[str]
    custom_fields: list[CustomFieldSpec] = field(default_factory=list)


CHECKLIST_ITEMS: list[ChecklistItemSpec] = [
    # Domain 1: 런타임 & 프로브 (Weight 1x)
    ChecklistItemSpec(
        id="C1.1",
        code_alias="REL-01",
        domain_id="D1",
        domain_name="1. 런타임 & 프로브",
        domain_weight=1,
        title="인프라 코드(IaC) 기반 런타임 선언 & 드리프트 방지 (service.tf)",
        severity="Critical",
        before_summary="Terraform IaC 미생성 또는 콘솔 수동 배포 상태",
        after_criteria="google_cloud_run_v2_service.app (Cloud Run) 또는 google_vertex_ai_reasoning_engine.app (Agent Runtime) IaC 및 lifecycle.ignore_changes 선언 (사용자 CPU/Memory 보존)",
        why_it_matters="엔터프라이즈 에이전트는 Terraform(service.tf) 단일 코드로 관리하고 lifecycle.ignore_changes로 CI/CD 이미지 배포와 인프라 상태 간 충돌을 방지해야 합니다. 단, CPU/메모리/min_instances는 임의 상향하지 않고 기존 값을 보존합니다.",
        verify_cmd='grep -nE "google_cloud_run_v2_service|google_vertex_ai_reasoning_engine" deployment/terraform/service.tf && grep -n "ignore_changes" deployment/terraform/service.tf',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="google_vertex_ai_reasoning_engine.app 관리형 런타임 리소스 및 lifecycle.ignore_changes 보존",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf 내 google_cloud_run_v2_service.app 및 lifecycle.ignore_changes 표준 IaC 구성 (기존 CPU/Memory 보존)",
        gke_desc="kubernetes_deployment_v1 및 Service 매니페스트 구성",
        target_files=["deployment/terraform/service.tf"],
        custom_fields=[
            CustomFieldSpec(
                key="workload_type",
                label="Cloud Run 리소스 형태",
                default="service",
                options=["service", "worker_pool", "job"],
            )
        ],
    ),
    ChecklistItemSpec(
        id="C1.2",
        code_alias="REL-02",
        domain_id="D1",
        domain_name="1. 런타임 & 프로브",
        domain_weight=1,
        title="콜드스타트 장애 방지용 헬스체크 프로브 & Startup CPU Boost",
        severity="Standard",
        before_summary="Cloud Run 컨테이너 startup_probe / liveness_probe 및 startup_cpu_boost 미구성",
        after_criteria="service.tf 내 startup_probe + liveness_probe (/list-apps:8080) 및 startup_cpu_boost = true 구성",
        why_it_matters="모델/플러그인 초기화가 끝나기 전 트래픽이 유입되면 초기 502/503 에러가 발생하며, 데드락에 빠진 컨테이너를 자동 교체하려면 Liveness 프로브가 필수입니다. startup_cpu_boost는 상시 과금 없이 Python/ADK 기동 시간을 단축합니다.",
        verify_cmd='gcloud run services describe $SERVICE_NAME --region=$REGION --format="yaml(template.containers[0].startupProbe,template.containers[0].livenessProbe)"',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="Agent Runtime 플랫폼 내부 헬스체크 및 컨테이너 라이프사이클 자동 관리",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf 내 startup_probe, liveness_probe (/list-apps:8080) 및 startup_cpu_boost = true 자동 주입",
        gke_desc="Pod startupProbe, readinessProbe, livenessProbe 구성",
        target_files=["deployment/terraform/service.tf", "app/fast_api_app.py"],
    ),
    # Domain 2: 세션 & 장기 기억 (Weight 2x) — Agent Platform Managed 기본
    ChecklistItemSpec(
        id="C2.1",
        code_alias="MEM-01",
        domain_id="D2",
        domain_name="2. 세션 & 메모리",
        domain_weight=2,
        title="영속 멀티턴 세션 스토어 (Agent Platform Managed Sessions)",
        severity="Critical",
        before_summary="InMemorySessionService 사용 (인스턴스 재시작·스케일아웃 시 대화 맥락 즉시 증발)",
        after_criteria="Agent Platform Managed Session Service (VertexAiSessionService + GOOGLE_CLOUD_AGENT_ENGINE_ID) 기본 연동 (※ 특수 요건 시 Self-Hosted Cloud SQL/Redis 자체 구축 옵션 가능)",
        why_it_matters="인메모리 세션은 컨테이너가 2개 이상으로 스케일아웃되거나 재배포될 때 이전 대화가 사라집니다. Agent Platform Managed Session Service를 사용하면 별도 DB/Redis 운영 부담 없이 안정적인 멀티턴 영속 세션을 보장합니다.",
        verify_cmd='grep -n "VertexAiSessionService" app/app_utils/services.py && grep -n "GOOGLE_CLOUD_AGENT_ENGINE_ID" deployment/terraform/service.tf',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="Agent Runtime이 내장 Managed Session Service를 기본 바인딩하여 멀티턴 상태 자동 보존",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="services.py에 VertexAiSessionService 우선 연결 + service.tf에 GOOGLE_CLOUD_AGENT_ENGINE_ID 환경변수 구성",
        gke_desc="Agent Platform Managed Session Service 또는 Self-Hosted DB 연동",
        target_files=["app/app_utils/services.py", "deployment/terraform/service.tf"],
        custom_fields=[
            CustomFieldSpec(
                key="agent_engine_id",
                label="Agent Engine ID (Cloud Run에서 Managed Session/Memory Bank 연결용, 선택)",
                default="",
            ),
        ],
    ),
    ChecklistItemSpec(
        id="C2.2",
        code_alias="MEM-02",
        domain_id="D2",
        domain_name="2. 세션 & 메모리",
        domain_weight=2,
        title="크로스-세션 장기 기억 Memory Bank (Cross-Session Memory)",
        severity="Critical",
        before_summary="세션 종료 시 사용자 선호/맥락이 소실되며 MemoryService(Memory Bank) 미연동",
        after_criteria="Agent Platform Memory Bank (VertexAiMemoryBankService + shared://memory + after_agent_callback 자동 저장) 연동 (※ 자체 Vector DB 기반 Self-Hosted 구축 옵션도 가능)",
        why_it_matters="단일 세션이 끝나더라도 사용자 선호, 이전 해결 이력, 핵심 사실(Fact)을 교차 세션으로 기억하려면 Memory Bank 연동과 매 턴 종료 시 비동기 자동 저장 콜백이 필요합니다.",
        verify_cmd='grep -n "VertexAiMemoryBankService" app/app_utils/services.py && grep -n "auto_save_session_to_memory" app/agent.py',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="services.py에 VertexAiMemoryBankService 등록 + agent.py after_agent_callback 자동 저장 콜백 장착",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="services.py에 VertexAiMemoryBankService (shared://memory) 등록 + fast_api_app.py Runner 연결 + agent.py 자동 저장 콜백",
        gke_desc="동일한 VertexAiMemoryBankService 연동",
        target_files=["app/app_utils/services.py", "app/fast_api_app.py", "app/agent.py"],
    ),
    # Domain 3: 사내망 네트워크 (Weight 2x)
    ChecklistItemSpec(
        id="C3.1",
        code_alias="NET-01",
        domain_id="D3",
        domain_name="3. 사내망 네트워크",
        domain_weight=2,
        title="사내 VPC 프라이빗 아웃바운드 연결 (Direct VPC Egress / PSC-I)",
        severity="Critical",
        before_summary="사내 Private IP DB·내부 API 접근용 Direct VPC Egress 또는 PSC Interface 미구성",
        after_criteria="Cloud Run: Direct VPC Egress (var.vpc_network_name / var.vpc_subnet_name 바인딩) | Agent Runtime: PSC Interface (psc_interface_config) 연결",
        why_it_matters="에이전트 도구가 사내 프라이빗 DB나 내부 API를 공인 인터넷 없이 호출하려면, Cloud Run은 기존 사내 VPC/서브넷으로의 Direct VPC Egress 연결이, Agent Runtime은 PSC Interface(psc_interface_config) 연결이 필요합니다.",
        verify_cmd='grep -nE "vpc_access|psc_interface_config" deployment/terraform/service.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="service.tf에 psc_interface_config 바인딩 및 production_ready.tf PSC Network Attachment 구성",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf에 Direct VPC Egress(var.vpc_network_name / var.vpc_subnet_name) 바인딩 (기존 VPC/서브넷 재사용으로 CIDR 충돌 방지)",
        gke_desc="GKE Private Cluster 및 Dataplane V2 Egress 정책",
        target_files=["deployment/terraform/service.tf", "deployment/terraform/variables.tf"],
        custom_fields=[
            CustomFieldSpec("vpc_network_name", "대상 사내 VPC 네트워크 이름", "default"),
            CustomFieldSpec("vpc_subnet_name", "대상 사내 VPC 서브넷 이름 (Cloud Run Direct VPC)", "default"),
            CustomFieldSpec(
                "egress_mode",
                "Cloud Run VPC Egress 라우팅 모드",
                "PRIVATE_RANGES_ONLY",
                ["PRIVATE_RANGES_ONLY", "ALL_TRAFFIC"],
            ),
        ],
    ),
    # Domain 4: 보안 & 격리 (Weight 2x) — AI 거버넌스(Model Armor/도구 방어)는 Agent Gateway Advisory로 분리
    ChecklistItemSpec(
        id="C4.1",
        code_alias="SEC-01",
        domain_id="D4",
        domain_name="4. 보안 & 격리",
        domain_weight=2,
        title="최소 권한 전용 서비스 계정(SA) & Secret 버전 고정",
        severity="Critical",
        before_summary="variables.tf에 과도한 roles/storage.admin 또는 미사용 roles/cloudsql.client 부여, Secret version='latest'",
        after_criteria="roles/storage.objectUser 축소 + 타겟에 불필요한 권한(roles/cloudsql.client 등) 제거 + Secret 버전 고정",
        why_it_matters="roles/storage.admin은 프로젝트 내 전체 버킷 삭제가 가능한 과도한 권한이며, 미사용 DB 권한이나 Secret version='latest' 참조는 침해 사고 및 시크릿 회전 장애 시 피해 반경을 키웁니다.",
        verify_cmd='grep -n "storage.objectUser" deployment/terraform/variables.tf && ! grep -n "storage.admin" deployment/terraform/variables.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="variables.tf의 roles/storage.admin을 roles/storage.objectUser로 축소 및 미사용 roles/cloudsql.client 제거",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="variables.tf 최소 권한(roles/storage.objectUser) 패치 및 service.tf Secret 버전 고정",
        gke_desc="Workload Identity 최소 권한 KSA->GSA 바인딩",
        target_files=["deployment/terraform/variables.tf", "deployment/terraform/service.tf"],
    ),
    ChecklistItemSpec(
        id="C4.3",
        code_alias="SEC-02",
        domain_id="D4",
        domain_name="4. 보안 & 격리",
        domain_weight=2,
        title="컨테이너 Non-Root(비특권) 실행 & Gen2 샌드박스",
        severity="Critical",
        before_summary="Dockerfile이 root(UID 0) 계정으로 실행 (컨테이너 탈취 시 루트 권한 노출)",
        after_criteria="Dockerfile 내 전용 appuser(UID 10001) 생성 및 USER 10001:10001 전환 (Cloud Run은 EXECUTION_ENVIRONMENT_GEN2 추가)",
        why_it_matters="에이전트가 외부 데이터나 도구를 실행할 때 컨테이너가 root(UID 0)로 동작하면 RCE 취약점 발생 시 컨테이너 루트 권한이 즉시 탈취됩니다. 비용 0원으로 즉시 적용해야 하는 기본 컨테이너 보안입니다.",
        verify_cmd='grep -n "USER 10001:10001" Dockerfile',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="Dockerfile 내 USER 10001:10001 Non-root 하드닝 적용",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="Dockerfile 내 USER 10001:10001 패치 및 service.tf 내 EXECUTION_ENVIRONMENT_GEN2 지정",
        gke_desc="Pod SecurityContext runAsNonRoot=true, runAsUser=10001",
        target_files=["Dockerfile", "deployment/terraform/service.tf"],
    ),
    # Domain 5: 관측성 & 알람 (Weight 2x)
    ChecklistItemSpec(
        id="C5.1",
        code_alias="OBS-01",
        domain_id="D5",
        domain_name="5. 관측성 & 알람",
        domain_weight=2,
        title="OpenTelemetry GenAI 분산 트레이싱 (Cloud Trace)",
        severity="Critical",
        before_summary="OTel GenAI 시맨틱 컨벤션 환경변수 또는 Cloud Trace 익스포터 미설정",
        after_criteria="OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental 및 otel_to_cloud=True 활성화",
        why_it_matters="멀티에이전트 및 도구 호출 구간에서 어디서 지연·오류가 발생했는지 추적하려면 OpenTelemetry GenAI 스팬이 Cloud Trace로 수집되어야 합니다.",
        verify_cmd='grep -n "gen_ai_latest_experimental" deployment/terraform/service.tf',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true 및 OTel GenAI 환경변수 플랫폼 기본 주입",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf 및 fast_api_app.py에 OTel GenAI 시맨틱 컨벤션 및 otel_to_cloud=True 구성",
        gke_desc="GKE OpenTelemetry Collector 및 Cloud Trace 연동",
        target_files=["deployment/terraform/service.tf", "app/fast_api_app.py"],
    ),
    ChecklistItemSpec(
        id="C5.2",
        code_alias="OBS-02",
        domain_id="D5",
        domain_name="5. 관측성 & 알람",
        domain_weight=2,
        title="트레이스 스팬 PII 평문 기록 차단 (NO_CONTENT)",
        severity="Critical",
        before_summary="Cloud Trace 스팬에 사용자 프롬프트·PII 원문 노출 위험 (ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS 미비)",
        after_criteria="OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 설정",
        why_it_matters="일반 개발자도 조회하는 Cloud Trace 스팬에는 지연시간·토큰 수 등 메타데이터만 남기고 고객 프롬프트·PII 원문은 기록하지 않아야 합니다.",
        verify_cmd='grep -n "NO_CONTENT" deployment/terraform/service.tf && grep -n "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS" deployment/terraform/service.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="google_vertex_ai_reasoning_engine env에 NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 패치",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf에 NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 패치",
        gke_desc="동일한 NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 환경변수 구성",
        target_files=["deployment/terraform/service.tf"],
    ),
    ChecklistItemSpec(
        id="C5.3",
        code_alias="OBS-03",
        domain_id="D5",
        domain_name="5. 관측성 & 알람",
        domain_weight=2,
        title="타겟별 SRE 골든 시그널 운영 알람 (alerts.tf)",
        severity="Critical",
        before_summary="Cloud Monitoring 알럿 정책(alerts.tf) 전무 (지연 급증·5xx 에러율 상승 미감지)",
        after_criteria="Cloud Run: cloud_run_revision P95 지연 + PromQL 5xx 비율(>2%) | Agent Runtime: ReasoningEngine P95 지연 + PromQL 5xx 비율(>2%)",
        why_it_matters="프로덕션 배포 후 LLM 지연 급증과 5xx 에러 비율 상승(>2%)을 SRE 팀이 실시간 감지하려면 런타임별 올바른 리소스 메트릭과 비율(Ratio) 수식으로 Alert Policy를 코드화해야 합니다.",
        verify_cmd='grep -n "google_monitoring_alert_policy" deployment/terraform/alerts.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="deployment/terraform/alerts.tf에 aiplatform.googleapis.com/ReasoningEngine P95 지연 및 PromQL 5xx 비율 알럿 생성",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="deployment/terraform/alerts.tf에 cloud_run_revision P95 지연 및 PromQL 5xx 비율(>2%) 알럿 생성",
        gke_desc="Managed Prometheus (GMP) PromQL 기반 SRE 알람 정책",
        target_files=["deployment/terraform/alerts.tf"],
        custom_fields=[
            CustomFieldSpec("latency_p95_ms", "P95 지연 알람 임계치 (ms, 멀티스텝 에이전트 기본 15000)", "15000"),
        ],
    ),
    # Domain 6: 비용 & 회복력 (Weight 1x) — 순수 루프 방어 & Google Cloud 공식 429 회복력 BP
    ChecklistItemSpec(
        id="C6.1",
        code_alias="FIN-01",
        domain_id="D6",
        domain_name="6. 비용 & 회복력",
        domain_weight=1,
        title="무한 도구 루프 차단(턴별 리셋) & 선택적 출력 토큰 상한 (FinOps)",
        severity="Critical",
        before_summary="단일 턴당 LLM 호출 횟수 상한 미구성 (도구 오류 시 무한 루프 과금 폭주 위험)",
        after_criteria="CostTokenGuardrailPlugin (invocation_id 기준 턴별 호출 횟수 자동 리셋 + 선택적 max_output_tokens 캡, 기본 0으로 Thinking 모델 안전 보존)",
        why_it_matters="에이전트가 도구 오류로 재시도 루프에 빠지면 단일 사용자 질문 한 번에 수십~수백 번 LLM을 호출합니다. invocation_id 단위로 매 턴 리셋되는 호출 상한을 장착하되, max_output_tokens는 기본 0(미강제)으로 두어 Gemini 2.5/3.x Thinking 모델의 추론(thoughts) 토큰 잘림 현상을 방지합니다.",
        verify_cmd='grep -n "CostTokenGuardrailPlugin" app/agent.py && grep -n "invocation_id" app/app_utils/cost_guard_plugin.py',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="cost_guard_plugin.py (invocation_id 턴별 리셋 + 선택적 max_output_tokens) 생성 및 agent.py 등록",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="cost_guard_plugin.py (invocation_id 턴별 리셋 + 선택적 max_output_tokens) 생성 및 agent.py 등록",
        gke_desc="동일한 CostTokenGuardrailPlugin 등록",
        target_files=["app/app_utils/cost_guard_plugin.py", "app/agent.py"],
        custom_fields=[
            CustomFieldSpec("max_llm_calls_per_turn", "단일 턴당 최대 LLM 호출 횟수 제한", "10"),
            CustomFieldSpec("max_output_tokens", "응답당 최대 출력 토큰 상한 (0=비활성 · Thinking 모델 안전 권장)", "0"),
        ],
    ),
    ChecklistItemSpec(
        id="C6.2",
        code_alias="REL-03",
        domain_id="D6",
        domain_name="6. 비용 & 회복력",
        domain_weight=1,
        title="Gemini API 429(DSQ) 방어: Global 엔드포인트 & 지수 백오프 재시도",
        severity="Standard",
        before_summary="HttpRetryOptions 미설정 또는 GOOGLE_CLOUD_LOCATION 미지정 (일시적 429/5xx 대응 부재)",
        after_criteria="GOOGLE_CLOUD_LOCATION (기본 global DSQ, 레지던시 규제 시 리전 유지) + HttpRetryOptions 지수 백오프·Jitter (기존 Fail-Fast attempts=3 보존)",
        why_it_matters="데이터 레지던시 제약이 없는 워크로드는 Global DSQ(global) 엔드포인트로 리전 편중 429를 완화하고, SDK 지수 백오프+Jitter 재시도를 사용해야 합니다. 단, 대화형 에이전트에서 무리하게 attempts를 늘리면 Retry Storm과 Cloud Run 동시성 고갈을 유발하므로 기본 Fail-Fast(attempts=3)를 보존하며, 상용 SLA 워크로드는 Provisioned Throughput(GSU) 또는 Priority PayGo를 검토하세요.",
        verify_cmd='grep -n "HttpRetryOptions" app/agent.py && grep -n "GOOGLE_CLOUD_LOCATION" deployment/terraform/service.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="agent.py에 HttpRetryOptions가 없을 때만 Fail-Fast 지수 백오프(attempts=3, jitter=1.0) 주입(기존 attempts>=3 보존) 및 service.tf GOOGLE_CLOUD_LOCATION 설정",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="agent.py에 HttpRetryOptions가 없을 때만 Fail-Fast 지수 백오프(attempts=3, jitter=1.0) 주입(기존 attempts>=3 보존) 및 service.tf GOOGLE_CLOUD_LOCATION 설정",
        gke_desc="동일한 HttpRetryOptions(Fail-Fast 지수 백오프) 및 모델 엔드포인트 리전 설정",
        target_files=["app/agent.py", "deployment/terraform/service.tf"],
        custom_fields=[
            CustomFieldSpec(
                "model_location",
                "Gemini 엔드포인트 리전 (global=DSQ 권장, 레지던시 규제 시 us-central1/asia-northeast3 등)",
                "global",
            ),
        ],
    ),
    # Domain 7: Day-2 CI/CD (Weight 1x)
    ChecklistItemSpec(
        id="C7.1",
        code_alias="OPS-01",
        domain_id="D7",
        domain_name="7. Day-2 CI/CD",
        domain_weight=1,
        title="CI/CD 공식 agents-cli eval run 배포 품질 게이트 (Eval Flywheel)",
        severity="Critical",
        before_summary=".cloudbuild/staging.yaml에 agents-cli eval run 품질 검증 게이트 및 평가 설정 누락",
        after_criteria=".cloudbuild/staging.yaml에 공식 agents-cli eval run (--dataset, --metrics) 게이트 + tests/eval/eval_config.yaml + 범용 스모크 데이터셋 구성",
        why_it_matters="프롬프트나 도구를 수정하면 일반 유닛 테스트는 통과해도 답변 품질·도구 호출 정확도가 급락할 수 있습니다. 스테이징 배포 직후 공식 agents-cli eval run 자동 품질 게이트로 회귀를 검증해야 합니다.",
        verify_cmd='grep -n "agent_eval_gate" .cloudbuild/staging.yaml && ls -l tests/eval/eval_config.yaml',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc=".cloudbuild/staging.yaml에 공식 agents-cli eval run 게이트 + eval_config.yaml + 스모크 데이터셋 생성",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc=".cloudbuild/staging.yaml에 공식 agents-cli eval run 게이트 + eval_config.yaml + 스모크 데이터셋 생성",
        gke_desc="동일한 Cloud Build agent_eval_gate 파이프라인 스텝 구성",
        target_files=[".cloudbuild/staging.yaml", "tests/eval/eval_config.yaml", "tests/eval/datasets/smoke_dataset.json"],
        custom_fields=[
            CustomFieldSpec(
                "eval_metrics",
                "agents-cli eval run 평가 지표 (쉼표 구분)",
                "final_response_quality,safety",
            ),
        ],
    ),
]

ITEM_BY_ID: dict[str, ChecklistItemSpec] = {item.id: item for item in CHECKLIST_ITEMS}
ITEM_BY_ALIAS: dict[str, ChecklistItemSpec] = {item.code_alias: item for item in CHECKLIST_ITEMS}

# Backward-compatible mapping from earlier 20-item / 17-item / 14-item IDs to the streamlined 13-item IDs
LEGACY_ID_MAP: dict[str, str] = {
    "C1.3": "C1.2",  # Health probes moved into C1.2
    "C2.3": "C2.1",  # GCS bucket versioning removed; maps to D2 session store
    "C3.2": "C3.1",  # Ingress WAF/IAP moved to non-scoring advisory; maps to D3 networking
    "C3.3": "C3.1",  # Private Google Access merged into C3.1
    "C4.2": "C4.1",  # Model Armor / tool guard moved to Agent Gateway governance advisory; maps to D4
    "C4.4": "C4.1",  # Tool SSRF/IDOR moved to governance advisory; maps to D4
    "C7.2": "C1.1",  # lifecycle.ignore_changes consolidated into C1.1
    "REL-00": "C1.1",
    "MEM-03": "C2.1",
    "GRD-01": "C4.1",
    "SEC-03": "C4.1",
    "SEC-04": "C4.1",
    "SEC-05": "C4.3",
    "OPS-02": "C1.1",
}


def resolve_item_ids(requested: list[str] | None) -> list[str]:
    """Resolves 'ALL', canonical C-ids ('C1.1'), aliases, or legacy IDs to active 13-item C-ids."""
    if not requested or "ALL" in [r.upper() for r in requested]:
        return [item.id for item in CHECKLIST_ITEMS]
    resolved: list[str] = []
    seen: set[str] = set()
    for token in requested:
        clean = token.strip().upper()
        mapped = LEGACY_ID_MAP.get(clean, clean)
        target_id: str | None = None
        if mapped in ITEM_BY_ID:
            target_id = mapped
        elif mapped in ITEM_BY_ALIAS:
            target_id = ITEM_BY_ALIAS[mapped].id
        if target_id and target_id not in seen:
            seen.add(target_id)
            resolved.append(target_id)
    return resolved
