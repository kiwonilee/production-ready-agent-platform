# 7-Domain, 15-Item Production-Readiness Rubric (`checklist-rubric.md`, v2.1)

## 1. Domain Weights & Scoring Formula

Each active item scores `100` (`GREEN`), `50` (`AMBER`), or `0` (`RED`). Items skipped (`N/A`) for a given workload profile are excluded from the denominator.

| Domain | Name | Weight | Core Items |
| :---: | :--- | :---: | :--- |
| **D1** | 런타임 & 프로브 (Runtime & Probes) | `1x` | `C1.1`, `C1.2` |
| **D2** | 세션 & 장기 기억 (Session & Memory) | `2x` | `C2.1`, `C2.2` |
| **D3** | 사내망 네트워크 (Private Networking) | `2x` | `C3.1` |
| **D4** | 보안, 신원 & 거버넌스 (Security, Identity & Governance) | `2x` | `C4.1`, `C4.2`, `C4.3`, `C4.4` |
| **D5** | 관측성, 프라이버시 & SRE 알람 (Observability & Alerts) | `2x` | `C5.1`, `C5.2`, `C5.3` |
| **D6** | 비용 통제 & 쿼터 회복력 (FinOps & Resilience) | `1x` | `C6.1`, `C6.2` |
| **D7** | Day-2 품질 평가 & CI/CD (Eval & CI/CD) | `1x` | `C7.1` |

---

## 2. Cloud Run vs. Agent Runtime Matrix (15 Core Items)

| ID | Title | Cloud Run (`cloud_run`) | Agent Runtime (`agent_runtime`) |
| :---: | :--- | :--- | :--- |
| **`C1.1`** | 런타임 IaC 선언 & 드리프트 방지 | `google_cloud_run_v2_service.app` + `lifecycle.ignore_changes` | 🟢 **MANAGED_NATIVE** (`google_vertex_ai_reasoning_engine.app`) |
| **`C1.2`** | 헬스체크 프로브 & Startup CPU Boost | `/list-apps:8080` `startup_probe` · `liveness_probe` + `startup_cpu_boost = true` | 🟢 **MANAGED_NATIVE** (플랫폼 내장 헬스체크) |
| **`C2.1`** | 영속 멀티턴 세션 스토어 | `VertexAiSessionService` + `AGENT_ENGINE_SESSION_ID` *(Cloud Run OTel 리소스 충돌 방지)* | 🟢 **MANAGED_NATIVE** (내장 Managed Session Service) |
| **`C2.2`** | 크로스-세션 장기 기억 (Memory Bank) | `VertexAiMemoryBankService` + `after_agent_callback` | `VertexAiMemoryBankService` + `after_agent_callback` |
| **`C3.1`** | 사내 VPC 프라이빗 아웃바운드 연결 | Direct VPC Egress (`var.vpc_network_name` / `var.vpc_subnet_name`) | PSC Interface (`psc_interface_config` + `google_compute_network_attachment`) |
| **`C4.1`** | 최소 권한 전용 SA & Secret 버전 고정 | `roles/storage.objectUser` 축소 + Secret 버전 고정 | `roles/storage.objectUser` 축소 + 미사용 `roles/cloudsql.client` 제거 |
| **`C4.2`** | **Agent Identity (SPIFFE ID) & Agent Registry 자동 등록** | `functional_type = "FUNCTIONAL_TYPE_AGENT"` + `identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"` + `agentregistry.googleapis.com` *(SA와 상호 배타)* | `spec.identity_type = "AGENT_IDENTITY"` + `agentregistry.googleapis.com` *(SA와 상호 배타)* |
| **`C4.3`** | 컨테이너 Non-Root & Gen2 샌드박스 | `Dockerfile` `USER 10001:10001` + `EXECUTION_ENVIRONMENT_GEN2` | `Dockerfile` `USER 10001:10001` |
| **`C4.4`** | **Agent Gateway 중앙 정책 게이트웨이** | 🔵 **Comment Advisory (`TIER_2_TEMPLATE`)** — 인프라 강제 생성 없이 `service.tf`에 단계적 도입 주석만 반영 *(Direct VPC + SWP/VPC-SC + IAP + Model Armor GA 보안 위 단계적 적용)* | `google_network_services_agent_gateway` (`AGENT_TO_ANYWHERE`) + IAP V2 `AuthzExtension`/`AuthzPolicy` (기본 **`DRY_RUN`**으로 HTTP 498 차단 방지) + `agent_gateway_config` + `Dockerfile` `AGENT_GATEWAY_ROOT_CERTIFICATES` |
| **`C5.1`** | OpenTelemetry 분산 트레이싱 | `OTEL_SEMCONV_STABILITY_OPT_IN` + `otel_to_cloud=True` + `google-adk[gcp,otel-gcp]` + `roles/monitoring.metricWriter` | 🟢 **MANAGED_NATIVE** (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`) |
| **`C5.2`** | 트레이스 `NO_CONTENT` 프라이버시 | `NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` | 동일 적용 |
| **`C5.3`** | 타겟별 SRE 골든 시그널 알람 | `cloud_run_revision` PromQL 5xx(`>2%`) + P95 지연(`15s`) 알람 | `ReasoningEngine` PromQL 5xx(`>2%`) + P95 지연(`15s`) 알람 |
| **`C6.1`** | 턴별 무한 루프 차단 & 선택적 토큰 상한 | `CostTokenGuardrailPlugin` (`invocation_id` 리셋 · `max_output_tokens=0` 기본) | 동일 플러그인 적용 |
| **`C6.2`** | Gemini 429(DSQ) 방어 & 지수 백오프 | `GOOGLE_CLOUD_LOCATION=global` + `HttpRetryOptions` (Fail-Fast `attempts>=3` 보존) | 동일 적용 |
| **`C7.1`** | 에이전트 자동 품질 평가 (`agents-cli eval run`) · CI/CD 연동 | `eval_config.yaml` + `smoke_dataset.json` (기존 `.cloudbuild/staging.yaml` 존재 시에만 게이트 삽입) | 동일 적용 |

---

## 3. Non-Scoring Governance & Architecture Advisories
The scanner intentionally keeps optional/org-policy decisions out of the 15-item score and surfaces them as **Advisories**:
- **Compute Sizing**: Preserve existing CPU/Memory/Concurrency/`min_instances`; size empirically via `tests/load_test/` (Locust).
- **Inbound Auth & Edge Topology**: Choose public URL + app OAuth/JWT vs IAM Invoker vs IAP vs GCLB + Cloud Armor WAF per security policy.
- **Model Armor & Cloud Run Staged Governance**: Enforce Model Armor Floor Settings centrally (or optional ADK `ModelArmorPlugin`), and on Cloud Run adopt Agent Identity / Registry / Self-Managed AGW incrementally on top of GA infra security (Direct VPC Egress + SWP/VPC-SC + IAP + Model Armor).
- **Production SLA / 429 Capacity**: Standard PayGo (DSQ) has no SLA; use **Provisioned Throughput (GSU + Shared spillover)** or **Priority PayGo** for SLA workloads, and **Flex PayGo / Batch API** for offline batch/eval.
