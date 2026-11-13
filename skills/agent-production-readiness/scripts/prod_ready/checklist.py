"""Canonical 7-Domain, 17-Item Production-Readiness Checklist, Guide & Customization Schema (v2).

Defines the 7 Domains, 17 streamlined Checklist Items (C1.1-C7.2), customizable parameters,
and explicit runtime mappings separating Cloud Run (`cloud_run`) and Agent Runtime (`agent_runtime`).

Key v2 Principles:
1. No unsolicited CPU/Memory/Concurrency/min_instances upsizing — sizing belongs to load testing (`tests/load_test/`).
2. Domain 2 defaults to Agent Platform Managed Session Service (`VertexAiSessionService`) and
   Memory Bank (`VertexAiMemoryBankService`); Self-Hosted Cloud SQL / Redis is noted only as an optional alternative.
3. Strict runtime separation between Cloud Run and Agent Runtime (no Cloud Run scripts/metrics leaked into Agent Runtime).
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
    subnet_cidr: str = "10.128.64.0/26"
    egress_mode: str = "PRIVATE_RANGES_ONLY"
    ingress_mode: str = "external_waf"  # "external_waf" | "internal_iap"
    waf_rate_limit_rpm: int = 100
    enable_bq_analytics: bool = False
    latency_p95_ms: int = 15000
    model_location: str = "global"
    max_llm_calls_per_turn: int = 10
    max_output_tokens: int = 2048
    monthly_budget_usd: int = 1000
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
        code_alias="REL-00",
        domain_id="D1",
        domain_name="1. 런타임 & 프로브",
        domain_weight=1,
        title="워크로드별 IaC 리소스 정의 (service.tf)",
        severity="Standard",
        before_summary="Terraform IaC 미생성 또는 콘솔 수동 배포 상태",
        after_criteria="google_cloud_run_v2_service.app (Cloud Run) 또는 google_vertex_ai_reasoning_engine.app (Agent Runtime) IaC 정의 (리소스 CPU/Memory/Concurrency는 사용자 설정 존중)",
        why_it_matters="엔터프라이즈 에이전트는 IaC(Terraform)로 배포해야 재현성과 변경 감사가 가능합니다. 단, CPU/메모리/동시성/최소 인스턴스 수치는 에이전트 특성과 부하 테스트(tests/load_test/) 결과에 따라 사용자가 직접 결정해야 하므로 임의 상향하지 않습니다.",
        verify_cmd='terraform -chdir=deployment/terraform state list | grep -E "google_cloud_run_v2_service|google_vertex_ai_reasoning_engine"',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="google_vertex_ai_reasoning_engine.app 관리형 컨테이너 런타임 (스펙은 사용자 부하테스트 기반 유지)",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf 내 google_cloud_run_v2_service.app 표준 IaC 구성 (기존 CPU/Memory/Concurrency 값 보존)",
        gke_desc="kubernetes_deployment_v1 및 Service 매니페스트 구성 (Phase 2)",
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
        title="헬스체크 프로브 & Startup CPU Boost",
        severity="Standard",
        before_summary="Cloud Run 컨테이너 startup_probe / liveness_probe 및 startup_cpu_boost 미구성",
        after_criteria="service.tf 내 startup_probe + liveness_probe (/list-apps:8080) 및 startup_cpu_boost = true 구성",
        why_it_matters="모델/플러그인 초기화가 끝나기 전 트래픽이 유입되면 초기 503 에러가 발생하며, 데드락에 빠진 컨테이너를 자동 교체하려면 Liveness 프로브가 필수입니다. startup_cpu_boost는 상시 과금 없이 Python/ADK 기동 시간을 단축합니다.",
        verify_cmd='gcloud run services describe $SERVICE_NAME --region=$REGION --format="yaml(template.containers[0].startupProbe,template.containers[0].livenessProbe)"',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="Agent Runtime 플랫폼 내부 헬스체크 및 컨테이너 라이프사이클 자동 관리",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf 내 startup_probe, liveness_probe 및 startup_cpu_boost = true 자동 주입",
        gke_desc="Pod startupProbe, readinessProbe, livenessProbe 및 preStop hook 구성",
        target_files=["deployment/terraform/service.tf", "app/fast_api_app.py"],
    ),
    # Domain 2: 세션 & 메모리 (Weight 2x) — Agent Platform Managed 기본
    ChecklistItemSpec(
        id="C2.1",
        code_alias="MEM-01",
        domain_id="D2",
        domain_name="2. 세션 & 메모리",
        domain_weight=2,
        title="영속 멀티턴 세션 스토어 (Agent Platform Managed Sessions)",
        severity="Critical",
        before_summary="InMemorySessionService 사용 (인스턴스 재시작·스케일아웃 시 대화 맥락 즉시 증발)",
        after_criteria="Agent Platform Managed Session Service (VertexAiSessionService + GOOGLE_CLOUD_AGENT_ENGINE_ID) 기본 연동 (※ 폐쇄망 DB 등 특수 요건 시 Self-Hosted Cloud SQL/Redis 자체 구축 옵션 가능)",
        why_it_matters="인메모리 세션은 컨테이너가 2개 이상으로 스케일아웃되거나 재배포될 때 이전 대화가 사라집니다. Agent Platform Managed Session Service를 사용하면 별도 DB/Redis 운영이나 VPC 설정 없이도 Cloud Run과 Agent Runtime 모두에서 안정적인 멀티턴 영속 세션을 보장합니다.",
        verify_cmd='grep -n "VertexAiSessionService" app/app_utils/services.py && grep -n "GOOGLE_CLOUD_AGENT_ENGINE_ID" deployment/terraform/service.tf',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="Agent Runtime이 GOOGLE_CLOUD_AGENT_ENGINE_ID를 자동 주입해 VertexAiSessionService 기본 연동",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="services.py에 VertexAiSessionService 우선 연결 + service.tf에 GOOGLE_CLOUD_AGENT_ENGINE_ID 환경변수 구성 (Self-Hosted는 선택 참고)",
        gke_desc="Agent Platform Managed Session Service (agentengine://) 또는 Self-Hosted DB 연동",
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
        title="장기 기억 Memory Bank (Cross-Session Memory)",
        severity="Critical",
        before_summary="세션 종료 시 사용자 선호/맥락이 소실되며 MemoryService(Memory Bank) 미연동",
        after_criteria="Agent Platform Memory Bank (VertexAiMemoryBankService + shared://memory + after_agent_callback 자동 저장) 연동 (※ 자체 Vector DB 기반 Self-Hosted 구축 옵션도 가능)",
        why_it_matters="단일 세션이 끝나더라도 사용자 선호, 이전 해결 이력, 핵심 사실(Fact)을 교차 세션으로 기억하려면 Memory Bank 연동이 필요합니다. Agent Platform Memory Bank는 임베딩·사실 추출·통합을 완전 관리형으로 제공합니다.",
        verify_cmd='grep -n "VertexAiMemoryBankService" app/app_utils/services.py && grep -n "add_session_to_memory" app/agent.py',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="services.py에 VertexAiMemoryBankService 등록 + agent.py 자동 저장 콜백 + service.tf Memory Bank 연동 가이드",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="services.py에 VertexAiMemoryBankService (shared://memory) 등록 + fast_api_app.py Runner 연결 + agent.py 자동 저장 콜백",
        gke_desc="동일한 VertexAiMemoryBankService 연동",
        target_files=["app/app_utils/services.py", "app/fast_api_app.py", "app/agent.py"],
    ),
    ChecklistItemSpec(
        id="C2.3",
        code_alias="MEM-03",
        domain_id="D2",
        domain_name="2. 세션 & 메모리",
        domain_weight=2,
        title="GCS 아티팩트/로그 버킷 내구성 (Versioning & Soft Delete)",
        severity="Standard",
        before_summary="아티팩트·로그 GCS 버킷에 객체 버전 관리(Versioning)와 Soft Delete 미구성",
        after_criteria="storage.tf 내 google_storage_bucket.logs_data_bucket에 versioning 및 7일 soft_delete_policy 활성화",
        why_it_matters="에이전트가 생성한 리포트·파일 아티팩트와 감사 로그 버킷에 객체 버전 관리 및 7일 Soft Delete가 없으면 덮어쓰기나 오삭제 시 복구가 불가능합니다.",
        verify_cmd='gcloud storage buckets describe gs://${PROJECT_ID}-${PROJECT_NAME}-logs --format="json(versioning,soft_delete_policy)"',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="storage.tf 내 GCS 아티팩트 버킷 Versioning 및 7일 Soft Delete 활성화",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="storage.tf 내 GCS 아티팩트 버킷 Versioning 및 7일 Soft Delete 활성화",
        gke_desc="GCS Fuse CSI 또는 GcsArtifactService 버킷 Versioning/Soft-Delete 적용",
        target_files=["deployment/terraform/storage.tf"],
    ),
    # Domain 3: 사내망 네트워크 & 인바운드 (Weight 2x) — 구 C3.3 통합
    ChecklistItemSpec(
        id="C3.1",
        code_alias="SEC-02",
        domain_id="D3",
        domain_name="3. 사내망 네트워크",
        domain_weight=2,
        title="사내망 프라이빗 아웃바운드 & Private Google Access",
        severity="Critical",
        before_summary="사내 Private IP DB·내부 API·온프레미스 접근용 Direct VPC Egress 또는 PSC Interface 미구성",
        after_criteria="Cloud Run: Direct VPC Egress + 전용 /26 서브넷(private_ip_google_access=true) | Agent Runtime: PSC Network Attachment + service.tf psc_interface_config 실제 연결",
        why_it_matters="에이전트가 사내 프라이빗 DB나 온프레미스 ERP/MCP 서버를 공인 인터넷 없이 안전하게 호출하려면, Cloud Run은 Direct VPC Egress, Agent Runtime은 Private Service Connect Interface(PSC-I)와 Private Google Access가 필수입니다.",
        verify_cmd='gcloud run services describe $SERVICE_NAME --region=$REGION --format="yaml(template.vpcAccess)" || grep -n "network_attachment" deployment/terraform/service.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="production_ready.tf에 PSC Network Attachment + private_ip_google_access 서브넷 생성 및 service.tf psc_interface_config 연결",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf에 Direct VPC Egress 주입 및 production_ready.tf에 전용 /26 서브넷(private_ip_google_access=true, VPC 변수화) 생성",
        gke_desc="GKE Private Cluster (enable_private_nodes) 및 Dataplane V2 Egress 정책",
        target_files=["deployment/terraform/service.tf", "deployment/terraform/production_ready.tf"],
        custom_fields=[
            CustomFieldSpec("vpc_network_name", "대상 VPC 네트워크 이름 (Shared VPC 시 변경)", "default"),
            CustomFieldSpec("subnet_cidr", "Direct VPC / PSC 서브넷 CIDR", "10.128.64.0/26"),
            CustomFieldSpec(
                "egress_mode",
                "Cloud Run VPC Egress 라우팅 모드",
                "PRIVATE_RANGES_ONLY",
                ["PRIVATE_RANGES_ONLY", "ALL_TRAFFIC"],
            ),
        ],
    ),
    ChecklistItemSpec(
        id="C3.2",
        code_alias="SEC-01",
        domain_id="D3",
        domain_name="3. 사내망 네트워크",
        domain_weight=2,
        title="인바운드 접근 통제 (대외: LB + Cloud Armor WAF / 사내: IAP · IAM)",
        severity="Critical",
        before_summary="Cloud Run ingress = INGRESS_TRAFFIC_ALL (공용 인터넷에 *.run.app 직접 노출)",
        after_criteria="대외 공개(external_waf): INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER + default_uri_disabled + Serverless NEG + Cloud Armor WAF | 사내 전용(internal_iap): INGRESS_TRAFFIC_INTERNAL_ONLY 또는 IAM/IAP 통제",
        why_it_matters="기본 INGRESS_TRAFFIC_ALL 설정은 누구나 *.run.app 기본 URL로 요청을 보낼 수 있어 DDoS·OWASP 공격에 노출됩니다. 대외 공개 서비스는 External HTTPS LB + Cloud Armor WAF 뒤로 격리하고, 사내 전용은 내부 인그레스 또는 IAP/IAM 인증을 적용해야 합니다.",
        verify_cmd='gcloud run services describe $SERVICE_NAME --region=$REGION --format="value(ingress,defaultUriDisabled)"',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="Agent Platform IAM 인증 기본 강제 (비인증 공용 URL 없음; 거버넌스 필요 시 Agent Gateway CLIENT_TO_AGENT 바인딩)",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="ingress_mode에 따라 대외 LB+NEG+Cloud Armor WAF 또는 사내 INTERNAL_ONLY/IAP 구성",
        gke_desc="GKE Gateway API + Cloud Armor BackendPolicy + IAP 구성",
        target_files=["deployment/terraform/service.tf", "deployment/terraform/production_ready.tf"],
        custom_fields=[
            CustomFieldSpec(
                "ingress_mode",
                "Cloud Run 인바운드 보호 모드",
                "external_waf",
                ["external_waf", "internal_iap"],
            ),
            CustomFieldSpec("waf_rate_limit_rpm", "Cloud Armor IP당 분당 요청 제한 (RPM)", "100"),
        ],
    ),
    # Domain 4: 보안 & 가드레일 (Weight 2x) — 구 C4.4를 C4.2에 통합
    ChecklistItemSpec(
        id="C4.1",
        code_alias="SEC-03",
        domain_id="D4",
        domain_name="4. 보안 & 가드레일",
        domain_weight=2,
        title="최소 권한 전용 SA · 삭제 보호 · Secret Manager 버전 고정",
        severity="Critical",
        before_summary="variables.tf에 과도한 roles/storage.admin 부여, deletion_protection=false, Secret version='latest'",
        after_criteria="roles/storage.objectUser 축소 + 타겟별 최소 API/권한만 부여 (Cloud Run은 deletion_protection=true 및 Secret 버전 고정 추가)",
        why_it_matters="roles/storage.admin은 프로젝트 내 모든 버킷 삭제가 가능한 과도한 권한이며, Secret version='latest'는 잘못된 시크릿 회전 시 전체 장애를 유발합니다. 또한 타겟에 불필요한 권한(예: Agent Runtime의 Cloud SQL 권한)을 제거해야 합니다.",
        verify_cmd='grep -n "storage.objectUser" deployment/terraform/variables.tf && ! grep -n "storage.admin" deployment/terraform/variables.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="variables.tf의 roles/storage.admin을 roles/storage.objectUser로 축소 + Agent Runtime 불필요 권한/API 제외",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="variables.tf 최소 권한 패치 + service.tf deletion_protection=true 및 Secret version 고정",
        gke_desc="Workload Identity Federation 최소 권한 KSA->GSA 바인딩 및 Secret CSI 드라이버",
        target_files=["deployment/terraform/variables.tf", "deployment/terraform/service.tf"],
    ),
    ChecklistItemSpec(
        id="C4.2",
        code_alias="GRD-01",
        domain_id="D4",
        domain_name="4. 보안 & 가드레일",
        domain_weight=2,
        title="Model Armor 입출력/PII & 도구 SSRF·호출자 신원(IDOR) 가드레일",
        severity="Critical",
        before_summary="입출력 가드레일 및 도구 SSRF/호출자 권한 검증 전무 (인젝션·PII 유출·169.254.169.254 SSRF 무방비)",
        after_criteria="ModelArmorGuardrailPlugin: 실제 Google Cloud Model Armor API(SanitizeUserPrompt/SanitizeModelResponse) + 로컬 인젝션/PII 폴백 + before_tool_callback SSRF/IDOR 차단",
        why_it_matters="사용자 입력이 세션/모델에 도달하기 전과 응답 반환 전 Model Armor API로 프롬프트 인젝션·PII를 차단하고, 도구 호출 직전 내부 메타데이터 IP(169.254.169.254) SSRF와 타 사용자 user_id 변조(IDOR/Confused Deputy)를 막아야 합니다.",
        verify_cmd='python3 -c "from app.app_utils.model_armor_plugin import ModelArmorGuardrailPlugin; print(ModelArmorGuardrailPlugin)"',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="model_armor_plugin.py (Model Armor API + SSRF/IDOR 가드) 등록 + roles/modelarmor.user + --agent-identity 안내",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="model_armor_plugin.py (Model Armor API + SSRF/IDOR 가드) 생성 및 app/agent.py _plugins 등록 + roles/modelarmor.user",
        gke_desc="동일한 ModelArmorGuardrailPlugin + GKE NetworkPolicy 구성",
        target_files=["app/app_utils/model_armor_plugin.py", "app/agent.py"],
    ),
    ChecklistItemSpec(
        id="C4.3",
        code_alias="SEC-05",
        domain_id="D4",
        domain_name="4. 보안 & 가드레일",
        domain_weight=2,
        title="컨테이너 Non-Root 하드닝 & Gen2 샌드박스",
        severity="Critical",
        before_summary="Dockerfile이 root(UID 0) 계정으로 실행 (컨테이너 탈취 시 루트 권한 노출)",
        after_criteria="Dockerfile 내 전용 appuser(UID 10001) 생성 및 USER 10001:10001 전환 (Cloud Run은 EXECUTION_ENVIRONMENT_GEN2 추가)",
        why_it_matters="에이전트가 외부 데이터나 도구를 실행할 때 컨테이너가 root(UID 0)로 동작하면 RCE 취약점 발생 시 컨테이너 전체가 장악됩니다. 추가 비용 0원으로 즉시 적용해야 하는 기본 보안입니다.",
        verify_cmd='grep -n "USER 10001:10001" Dockerfile',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="Dockerfile 내 USER 10001:10001 Non-root 하드닝 적용 (실행 샌드박스는 플랫폼 네이티브)",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="Dockerfile 내 USER 10001:10001 패치 및 service.tf 내 EXECUTION_ENVIRONMENT_GEN2 지정",
        gke_desc="Pod SecurityContext runAsNonRoot=true, runAsUser=10001 및 gVisor Sandbox",
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
        title="트레이스 프라이버시(NO_CONTENT) & 선택적 BigQuery 감사",
        severity="Critical",
        before_summary="Cloud Trace 스팬에 사용자 프롬프트·PII 원문 노출 위험 (ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS 미비)",
        after_criteria="OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 강제 (+ 선택 enable_bq_analytics=true 시 BigQueryAgentAnalyticsPlugin 연동)",
        why_it_matters="일반 개발자도 조회하는 Cloud Trace 스팬에는 프롬프트·PII 본문을 남기지 않아야(NO_CONTENT) 합니다. 감사·품질 분석용 구조화 로깅(BigQueryAgentAnalyticsPlugin)은 필요한 워크로드에서 선택(Opt-in) 활성화합니다.",
        verify_cmd='grep -n "NO_CONTENT" deployment/terraform/service.tf && grep -n "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS" deployment/terraform/service.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="google_vertex_ai_reasoning_engine env에 NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 패치 (+ 선택 BQ Analytics)",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf에 NO_CONTENT 및 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 강제 (+ 선택 BQ Analytics)",
        gke_desc="동일한 NO_CONTENT 가드 및 선택적 BigQueryAgentAnalyticsPlugin 구성",
        target_files=["deployment/terraform/service.tf", "app/agent.py"],
        custom_fields=[
            CustomFieldSpec(
                "enable_bq_analytics",
                "BigQuery Agent Analytics 플러그인 활성화 (선택 · BQ 데이터셋 필요)",
                "false",
                ["false", "true"],
            ),
        ],
    ),
    ChecklistItemSpec(
        id="C5.3",
        code_alias="OBS-03",
        domain_id="D5",
        domain_name="5. 관측성 & 알람",
        domain_weight=2,
        title="타겟별 SRE 골든 시그널 운영 알람 (alerts.tf)",
        severity="Critical",
        before_summary="Cloud Monitoring 알럿 정책(alerts.tf) 전무 (지연 급증·5xx 에러율·가드레일 차단 폭증 미감지)",
        after_criteria="Cloud Run: cloud_run_revision P95 지연 + PromQL 5xx 비율(>2%) + 가드레일 알람 | Agent Runtime: ReasoningEngine 메트릭 + 가드레일 알람",
        why_it_matters="프로덕션 배포 후 LLM 지연 급증, 에러 비율 상승(>2%), 프롬프트 인젝션 공격 급증을 SRE 팀이 실시간 감지하려면 런타임별 올바른 리소스 메트릭과 비율(Ratio) 수식으로 Alert Policy를 코드화해야 합니다.",
        verify_cmd='grep -n "google_monitoring_alert_policy" deployment/terraform/alerts.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="deployment/terraform/alerts.tf에 aiplatform.googleapis.com/ReasoningEngine 전용 메트릭 및 Guardrail 알럿 생성",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="deployment/terraform/alerts.tf에 cloud_run_revision P95 지연, PromQL 5xx 비율(>2%) 및 Guardrail 알럿 생성",
        gke_desc="Managed Prometheus (GMP) PromQL 기반 SRE 알람 정책",
        target_files=["deployment/terraform/alerts.tf"],
        custom_fields=[
            CustomFieldSpec("latency_p95_ms", "P95 지연 알람 임계치 (ms, 멀티스텝 에이전트 기본 15000)", "15000"),
        ],
    ),
    # Domain 6: 비용 & 회복력 (Weight 1x)
    ChecklistItemSpec(
        id="C6.1",
        code_alias="FIN-01",
        domain_id="D6",
        domain_name="6. 비용 & 회복력",
        domain_weight=1,
        title="무한 루프 방지(턴별 리셋) & 월간 예산 알람 (FinOps)",
        severity="Critical",
        before_summary="턴당 LLM 호출/토큰 상한 및 Cloud Billing Budget(budget.tf) 미구성 (Denial-of-Wallet 위험)",
        after_criteria="CostTokenGuardrailPlugin (invocation_id 기준 턴별 호출 횟수 리셋 + max_output_tokens 실제 적용) + 선택적 budget.tf",
        why_it_matters="멀티에이전트가 도구 오류로 무한 루프에 빠지면 단일 턴이 수백 번의 LLM 호출을 유발합니다. 턴 단위 호출 상한(invocation별 리셋), 출력 토큰 캡, 프로젝트 월간 예산 알람으로 과금 폭주를 차단해야 합니다.",
        verify_cmd='grep -n "CostTokenGuardrailPlugin" app/agent.py && grep -n "google_billing_budget" deployment/terraform/budget.tf',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="cost_guard_plugin.py (턴별 리셋 + max_output_tokens 강제) 및 선택적 budget.tf 생성",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="cost_guard_plugin.py (턴별 리셋 + max_output_tokens 강제) 및 선택적 budget.tf 생성",
        gke_desc="동일한 CostTokenGuardrailPlugin + budget.tf + K8s ResourceQuota",
        target_files=["app/app_utils/cost_guard_plugin.py", "deployment/terraform/budget.tf"],
        custom_fields=[
            CustomFieldSpec("max_llm_calls_per_turn", "단일 턴당 최대 LLM 호출 횟수 제한", "10"),
            CustomFieldSpec("max_output_tokens", "응답당 최대 출력 토큰 수 (max_output_tokens)", "2048"),
            CustomFieldSpec("monthly_budget_usd", "월간 프로젝트 예산 상한 (USD)", "1000"),
        ],
    ),
    ChecklistItemSpec(
        id="C6.2",
        code_alias="REL-03",
        domain_id="D6",
        domain_name="6. 비용 & 회복력",
        domain_weight=1,
        title="LLM API 429(DSQ) 쿼터 방어 & 지수 백오프",
        severity="Standard",
        before_summary="기본 attempts=3 또는 재시도 미설정 (피크 트래픽 시 429 Resource Exhausted 즉시 실패)",
        after_criteria="HttpRetryOptions(attempts=5, initial_delay=1.0, exp_base=2.0) 및 GOOGLE_CLOUD_LOCATION (기본 global DSQ, 데이터 레지던시 규제 시 리전 유지)",
        why_it_matters="트래픽 피크로 단일 리전 쿼터가 소진(429)될 때 지수 백오프 재시도와 글로벌 Dynamic Shared Quota(DSQ) 엔드포인트(또는 규제 준수 리전)를 구성해야 사용자 체감 실패율을 최소화할 수 있습니다.",
        verify_cmd='grep -n "attempts=5" app/agent.py',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc="app/agent.py HttpRetryOptions(attempts=5) 강화 및 service.tf GOOGLE_CLOUD_LOCATION 설정",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="app/agent.py HttpRetryOptions(attempts=5) 강화 및 service.tf GOOGLE_CLOUD_LOCATION 설정",
        gke_desc="동일한 HttpRetryOptions(attempts=5) 및 모델 엔드포인트 리전 설정",
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
        title="agents-cli eval run 배포 품질 게이트 (Eval Flywheel)",
        severity="Critical",
        before_summary=".cloudbuild/staging.yaml에 agents-cli eval run 품질 검증 게이트 및 평가 설정 누락",
        after_criteria=".cloudbuild/staging.yaml에 공식 agents-cli eval run (--dataset, --metrics) 게이트 + tests/eval/eval_config.yaml + 범용 스모크 데이터셋 구성",
        why_it_matters="프롬프트나 모델 버전을 수정하면 유닛 테스트는 통과해도 응답 품질·안전성이 퇴행할 수 있습니다. 스테이징 배포 파이프라인에 agents-cli eval run 자동 품질 게이트를 배치해야 합니다.",
        verify_cmd='grep -n "agent_eval_gate" .cloudbuild/staging.yaml && ls -l tests/eval/eval_config.yaml',
        agent_runtime_mode="TIER_1_AUTO",
        agent_runtime_desc=".cloudbuild/staging.yaml에 공식 agents-cli eval run 게이트 + eval_config.yaml + 범용 데이터셋 생성",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc=".cloudbuild/staging.yaml에 공식 agents-cli eval run 게이트 + eval_config.yaml + 범용 데이터셋 생성",
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
    ChecklistItemSpec(
        id="C7.2",
        code_alias="OPS-02",
        domain_id="D7",
        domain_name="7. Day-2 CI/CD",
        domain_weight=1,
        title="무중단 카나리 트래픽 분할 & 이전 리비전 롤백",
        severity="Standard",
        before_summary="Cloud Run 100% 즉시 전환 / Terraform ignore_changes 누락 또는 롤백 자동화 부재",
        after_criteria="Cloud Run: lifecycle.ignore_changes + scripts/canary_rollout.sh (10%->50%->100% & 이전 안정 리비전 롤백) | Agent Runtime: lifecycle.ignore_changes 보존 (트래픽 분할 N/A)",
        why_it_matters="Cloud Run에서는 신규 리비전을 10%->50%->100%로 점진 배포하고 장애 시 직전 안정 리비전으로 즉시 롤백해야 합니다. Agent Runtime은 리비전 트래픽 분할 대신 Terraform lifecycle.ignore_changes로 SDK/CI 배포와의 충돌을 방지합니다.",
        verify_cmd='grep -n "ignore_changes" deployment/terraform/service.tf && (ls -l scripts/canary_rollout.sh 2>/dev/null || true)',
        agent_runtime_mode="MANAGED_NATIVE",
        agent_runtime_desc="service.tf lifecycle.ignore_changes 보존 (Cloud Run 전용 카나리 스크립트는 미생성 · 재배포 롤백 사용)",
        cloud_run_mode="TIER_1_AUTO",
        cloud_run_desc="service.tf ignore_changes 보존 + scripts/canary_rollout.sh (10%->50%->100% & 이전 안정 리비전 즉시 롤백)",
        gke_desc="Argo Rollouts / GKE Gateway HTTPRoute weight 기반 카나리 배포",
        target_files=["deployment/terraform/service.tf", "scripts/canary_rollout.sh"],
    ),
]

ITEM_BY_ID: dict[str, ChecklistItemSpec] = {item.id: item for item in CHECKLIST_ITEMS}
ITEM_BY_ALIAS: dict[str, ChecklistItemSpec] = {item.code_alias: item for item in CHECKLIST_ITEMS}

# Backward-compatible mapping from v1 (20-item) IDs to v2 (17-item) IDs
LEGACY_ID_MAP: dict[str, str] = {
    "C1.3": "C1.2",  # Health probes moved into C1.2 (old C1.2 forced upsizing removed)
    "C3.3": "C3.1",  # Private Google Access merged into C3.1
    "C4.4": "C4.2",  # Tool SSRF/IDOR merged into C4.2
    "NET-03": "C3.1",
    "SEC-04": "C4.2",
}


def resolve_item_ids(requested: list[str] | None) -> list[str]:
    """Resolves 'ALL', canonical C-ids ('C1.1'), aliases ('SEC-01'), or v1 legacy IDs to v2 C-ids."""
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
