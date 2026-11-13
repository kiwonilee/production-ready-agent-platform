# Pre-Scan Intake Questionnaire & 17-Item Tailoring Specification (`C1.1` – `C7.2`, v2)

이 문서는 `agent-production-readiness` v2 스킬이 **`scan` 및 `simulate`를 실행하기 전에 반드시 사용자에게 먼저 확인해야 하는 항목별 질문, 적용/생략 판별 기준, 그리고 CLI 파라미터(`--items`, `--params`) 매핑 규칙**을 정의합니다.

---

## 1. 핵심 실행 원칙 (Pre-Scan Gate & v2 설계 철학)

1. **`scan` / `simulate` 실행 전 사전 선택 필수**: 사용자가 이미 프로필을 지정했거나 `"전체 항목(ALL) / 기본값"`을 요청한 경우가 아니라면, `prod_ready.py scan` / `simulate` 실행 전에 먼저 환경을 선택받습니다.
2. **첫 질문은 10줄 이내 초간단 형태 유지 (UX 핵심)**: Step 1에서는 항목 코드(`C1.1`–`C7.2`)나 세부 숫자 파라미터(`subnet_cidr`, `waf_rate_limit_rpm`, `latency_p95_ms`, `model_location` 등)를 나열하지 마세요.
3. **리소스 자동 상향 금지 (No Unsolicited Sizing)**: CPU · 메모리 · 동시성(`concurrency`) · `min_instances`는 사용자의 실제 부하 테스트 결과를 바탕으로 직접 조정해야 하므로 자동 상향하지 않으며, 스캔 시 비감점 권고(`Advisory`)로만 안내합니다.
4. **D2 Managed-First 원칙**: 멀티턴 세션(`C2.1`)과 장기 기억(`C2.2`)은 **Agent Platform Managed Sessions (`VertexAiSessionService`) & Memory Bank (`VertexAiMemoryBankService`)** 를 기본으로 제공합니다. (규제/기존 인프라 재사용이 필요한 경우에만 Self-Hosted Cloud SQL · Redis · Vector DB 옵션을 참고로 안내합니다.)
5. **세부 파라미터(`--params`)는 Step 3(`apply` 직전)에서만 확인**: 첫 진단에서는 선택된 프로필의 권장 기본값으로 `scan` / `simulate`를 먼저 수행하고, 실제 패치를 적용하기 직전(Step 3)에만 기본값 변경 여부를 한 줄로 확인합니다.

---

## 2. Step 1 표준 질의 방식 (Interactive UI 우선 / 텍스트 Fallback)

### Mode A · 대화형 객관식 도구 (`ask_question` / `ask_user`) 사용 시 (권장)
1. **1차 선택 (`Question 1/1`)** — 아래 4개 선택지 제공 (`Write-in / Other`에 `A, B, A, B`를 직접 입력하라고 지시하지 말 것!):
   - `대외 공개 엔터프라이즈 — 외부 고객용 · 전체 17개 풀 하드닝 + WAF (권장)`
   - `사내 업무용 운영 — 임직원 전용 · 전체 17개 (사내 IAP/VPC 격리, 외부 WAF 제외)`
   - `사내 경량 / PoC — 사내 테스트 · 단발성 · 핵심 8개 최소 비용`
   - `직접 맞춤 조합하기 (4가지 환경 질문으로 선택)`
2. 사용자가 **`직접 맞춤 조합하기`** 를 선택한 경우에만 **2차 선택 (`Question 1/4`–`4/4`)** 을 띄워 화살표+Enter로 고르게 합니다:
   - **Q1. 공개 범위**: `사내 임직원 전용` / `외부 고객 공개`
   - **Q2. 대화 기억**: `단발성 질의응답` / `멀티턴·장기기억 (Managed Sessions & Memory Bank)`
   - **Q3. 사내 백엔드 연동**: `Gemini / 외부 공개 API만 호출` / `사내 DB · VPC · 온프레미스 연동`
   - **Q4. 운영 단계 & 민감도**: `PoC / Dev · 비민감 데이터` / `실제 운영 · PII/기밀 포함`

### Mode B · 일반 텍스트 출력 시 (객관식 도구가 없는 CLI)

```text
진단 전 운영 환경만 간단히 선택해 주세요. 번호(1~3) 하나만 입력하시면 바로 진행합니다.

  [1] 사내 경량 / PoC        — 사내 테스트 · 단발성 · 핵심 8개 최소 비용
  [2] 사내 업무용 운영        — 임직원 전용 · 전체 17개 (사내 IAP/VPC 격리)
  [3] 대외 공개 엔터프라이즈  — 외부 고객용 · 전체 17개 풀 하드닝 + WAF (권장)

※ 직접 조합하려면 4개만 A/B로 답해주세요 (예: A, B, A, B):
  1. 공개 범위:  A) 사내 전용   / B) 외부 고객 공개
  2. 대화 기억:  A) 단발성      / B) 멀티턴·장기기억 (Managed)
  3. 사내 연동:  A) 외부 API만  / B) 사내 DB·VPC 연동
  4. 운영·민감:  A) PoC·비민감  / B) 실운영·PII 포함
```

### 답변 → `--items` & `--params` 자동 매핑 규칙 (내부 참조용)

- **사내 경량 / PoC** (`A, A, A, A`): `--items C1.1,C1.2,C4.1,C4.3,C5.1,C5.2,C6.1,C6.2` (8개)
- **사내 업무용 운영** (`A, B, B, B`): `--items ALL --params '{"ingress_mode":"internal_iap"}'` (17개 · Cloud Run은 Public LB/WAF 대신 `INGRESS_TRAFFIC_INTERNAL_ONLY`/IAP 적용; Agent Runtime은 `C3.2` 기본 `MANAGED_NATIVE`)
- **대외 공개 엔터프라이즈** (`B, B, B, B`): `--items ALL --params '{"ingress_mode":"external_waf"}'` (17개 · External LB + Serverless NEG + Cloud Armor WAF)
- **맞춤 4문항 합집합**: 기본선 `C1.1,C1.2,C4.1,C4.3,C5.1,C5.2,C6.1,C6.2` (8개)에서 시작해:
  - `Q1 = 외부 고객 공개` (`1=B`) → `+ C3.2,C4.2` + `ingress_mode="external_waf"`
  - `Q1 = 사내 임직원 전용` (`1=A`) AND `Q4 = 실제 운영` (`4=B`) → `+ C3.2` + `ingress_mode="internal_iap"`
  - `Q2 = 멀티턴·장기기억` (`2=B`) → `+ C2.1,C2.2,C2.3`
  - `Q3 = 사내 DB·VPC 연동` (`3=B`) → `+ C3.1`
  - `Q4 = 실제 운영·PII 포함` (`4=B`) → `+ C4.2,C5.3,C7.1,C7.2`

---

## 3. 전체 17개 항목별 상세 확인 명세 (`C1.1` – `C7.2`, 내부 참조 및 Step 3 튜닝용)

> **공통 권장 기본선 (Always-Recommended Baseline 8개)**: 추가 비용이 거의 없고 보안/안정성 기본기를 보장하는 `C1.1`, `C1.2`, `C4.1`, `C4.3`, `C5.1`, `C5.2`, `C6.1`, `C6.2`는 PoC를 포함한 모든 환경에서 기본 적용을 권장합니다.

| ID (`Alias`) | 항목명 | 실행 전 확인 질문 | Cloud Run 적용 방식 | Agent Runtime 적용 방식 | 연결 `--params` 키 (기본값) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **C1.1** (`REL-00`) | IaC 리소스 프리미티브 매핑 | 배포 대상을 Terraform IaC로 관리할까요? | `google_cloud_run_v2_service.app` 생성/유지 | 🟢 **MANAGED_NATIVE** (`google_vertex_ai_reasoning_engine.app`) | `workload_type` (`"service"`) |
| **C1.2** (`REL-01`) | 헬스체크 프로브 & Startup CPU Boost | 컨테이너 기동/생존 프로브(`/list-apps:8080`)와 `startup_cpu_boost`를 적용할까요? *(CPU/메모리/min_instances는 강제 상향 안 함)* | `startup_probe`, `liveness_probe`, `startup_cpu_boost = true` 추가 | 🟢 **MANAGED_NATIVE** (플랫폼 내장 헬스체크) | — |
| **C2.1** (`MEM-01`) | 멀티턴 영구 세션 (Managed Sessions) | 여러 인스턴스로 스케일아웃되어도 대화가 유지되어야 하나요? *(참고: Self-Hosted Cloud SQL/Redis도 별도 구축 가능)* | `VertexAiSessionService` + `GOOGLE_CLOUD_AGENT_ENGINE_ID` 연동 | 🟢 **MANAGED_NATIVE** (플랫폼 내장 세션 서비스) | — |
| **C2.2** (`MEM-02`) | 크로스-세션 장기 기억 (Memory Bank) | 이전 대화가 끝나도 다음 세션에서 사용자 선호·사실을 자동 회상해야 하나요? *(참고: Self-Hosted Vector DB도 별도 옵션)* | `VertexAiMemoryBankService` (`shared://memory`) + `after_agent_callback` | Cloud Run과 동일 (`VertexAiMemoryBankService` + 자동 저장 콜백) | — |
| **C2.3** (`MEM-03`) | 아티팩트 & 로그 버킷 내구성 | 생성 파일/리포트/감사 로그 GCS 버킷에 버전관리와 7일 Soft Delete가 필요한가요? | `storage.tf` `versioning` + 7일 `soft_delete_policy` | Cloud Run과 동일 (`storage.tf`) | — |
| **C3.1** (`NET-01`) | 프라이빗 아웃바운드 & PGA (구 C3.3 통합) | 사내 Private IP DB · 온프레미스 API 연동 및 Private Google Access가 필요한가요? | Direct VPC Egress (`PRIVATE_RANGES_ONLY`) + `/26` 서브넷(`private_ip_google_access`) | PSC Interface (`google_compute_network_attachment`) + `psc_interface_config` + `/28` PGA 서브넷 | `vpc_network_name` (`"default"`), `subnet_cidr`, `egress_mode` |
| **C3.2** (`NET-02`) | 제로트러스트 인그레스 & Cloud Armor WAF | 대외 공개(`external_waf`)인가요, 사내 임직원 전용(`internal_iap`)인가요? | `external_waf`: LB+NEG+Cloud Armor WAF / `internal_iap`: `INGRESS_TRAFFIC_INTERNAL_ONLY` | 🟢 **MANAGED_NATIVE** (IAM 인증 전용, 퍼블릭 URL 없음) | `ingress_mode` (`"external_waf"`), `waf_rate_limit_rpm` (`100`) |
| **C4.1** (`SEC-01`) | 최소권한 SA · 삭제보호 · Secret 고정 | `roles/storage.admin`을 `storage.objectUser`로 축소하고 삭제보호 및 Secret 버전 고정을 적용할까요? | `storage.objectUser` + `deletion_protection = true` + Secret 버전 고정 | `storage.objectUser` + 불필요한 `cloudsql.client` 제거 | — |
| **C4.2** (`SEC-02`) | Model Armor 입출력 & 도구 SSRF/IDOR (구 C4.4 통합) | 인젝션/탈옥/PII 실시간 차단과 도구 SSRF·`user_id` IDOR 검증이 필요한가요? | `ModelArmorGuardrailPlugin` (`google.cloud.modelarmor_v1` + PII 폴백 + `before_tool_callback`) | Cloud Run과 동일 (`app/agent.py` 플러그인 등록) | — |
| **C4.3** (`SEC-03`) | 컨테이너 Non-Root & Gen2 샌드박스 | Dockerfile 비루트(`USER 10001:10001`) 및 Gen2 샌드박스를 적용할까요? | `USER 10001` + `EXECUTION_ENVIRONMENT_GEN2` | `Dockerfile` `USER 10001` | — |
| **C5.1** (`OBS-01`) | OpenTelemetry GenAI 분산 트레이싱 | LLM/도구 호출 구간 지연시간을 Cloud Trace로 추적할까요? | `OTEL_SEMCONV_STABILITY_OPT_IN` + `otel_to_cloud = True` | 🟢 **MANAGED_NATIVE** (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`) | — |
| **C5.2** (`OBS-02`) | `NO_CONTENT` 프라이버시 & 선택적 BQ 분석 | 트레이스 프롬프트 평문 노출 차단(`NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`)을 적용할까요? BigQuery 분석 플러그인도 켤까요? | `NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` (+ opt-in BQ) | Cloud Run과 동일 (`service.tf` 환경변수 + opt-in BQ) | `enable_bq_analytics` (`false`), `bq_location` (`"US"`) |
| **C5.3** (`OBS-03`) | 타겟별 SRE 골든 시그널 알람 | 지연/에러율/가드레일 차단 급증 시 Cloud Monitoring 알람을 받을까요? | `cloud_run_revision` P95 지연(`15s`) · PromQL 5xx 비율(`>2%`) · Guardrail 알럿 | `aiplatform.googleapis.com/ReasoningEngine` 전용 메트릭 · Guardrail 알럿 | `latency_p95_ms` (`15000`) |
| **C6.1** (`FIN-01`) | 턴별 루프 상한 · 출력 토큰 캡 & 예산 | 턴당 최대 LLM 호출 횟수, 최대 출력 토큰, 월간 예산 알람(선택)을 얼마로 설정할까요? | `CostTokenGuardrailPlugin` (`invocation_id` 리셋 + `max_output_tokens`) + `budget.tf` | Cloud Run과 동일 | `max_llm_calls_per_turn` (`10`), `max_output_tokens` (`4096`), `monthly_budget_usd` (`1000`) |
| **C6.2** (`FIN-02`) | 429 DSQ 쿼터 방어 & 지수 백오프 | `429 Resource Exhausted` 방어용 `attempts=5` 지수 백오프와 엔드포인트 위치(`global` vs 단일 리전)를 지정할까요? | `HttpRetryOptions(attempts=5)` + `GOOGLE_CLOUD_LOCATION` | Cloud Run과 동일 | `model_location` (`"global"`) |
| **C7.1** (`OPS-01`) | CI/CD Eval 품질 게이트 | Cloud Build 파이프라인에 공식 `agents-cli eval run --dataset ... --metrics ...` 게이트를 넣을까요? | `.cloudbuild/staging.yaml` + `eval_config.yaml` + `smoke_dataset.json` | Cloud Run과 동일 | `eval_threshold` (`"0.85"`) |
| **C7.2** (`OPS-02`) | 무중단 카나리 배포 & 안전 롤백 | 신규 버전 배포 시 단계적 트래픽 분할(10→50→100%)과 이전 안정 리비전 롤백 스크립트가 필요한가요? | `lifecycle.ignore_changes` + `scripts/canary_rollout.sh` (`$PREVIOUS_REVISION` 복원) | 🟢 **MANAGED_NATIVE** (`lifecycle.ignore_changes` 보존; 카나리 스크립트 미생성) | — |

---

## 4. 4대 장애·공격 시뮬레이션(`SIM-1` – `SIM-4`) 사전 적용 조건

| 시나리오 ID | 시뮬레이션 내용 | 연관 체크포인트 | 필수 검증 대상 환경 | `SKIPPED (N/A)` 조건 |
| :--- | :--- | :--- | :--- | :--- |
| **SIM-1** | 프롬프트 인젝션 & 시스템 프롬프트/PII 탈취 | `C4.2` | 사용자 입력 또는 PII를 다루는 모든 환경 | `C4.2` 제외 시 |
| **SIM-2** | 도구 악용 메타데이터 SSRF(`169.254.169.254`) & 퍼블릭 노출 | `C4.2`, `C3.2` | 도구 호출이 있거나 대외 노출되는 환경 | `C4.2`와 `C3.2` 모두 제외 시 |
| **SIM-3** | 에이전트 무한 도구 루프 & 월간 과금 폭탄 | `C6.1` | Tool-calling 에이전트 공통 | `C6.1` 제외 시 |
| **SIM-4** | 컨테이너 재시작·스케일아웃 시 멀티턴 세션 & 장기기억 유지 | `C2.1`, `C2.2` | 멀티턴 Stateful 에이전트 | 단발성 Stateless(`C2.1`, `C2.2` 모두 제외) 시 |

---

## 5. 확정된 답변을 CLI 명령으로 전달하는 예시

예: 사용자가 **"Cloud Run, 사내 임직원 전용, 멀티턴, 사내 DB 연동, PII/실운영, 리전 `asia-northeast3`, 월 예산 `$500`, 턴당 최대 호출 `8회`"** 라고 답한 경우:

```bash
export TARGET="cloud_run"
export ITEMS="ALL"
export PARAMS='{"ingress_mode":"internal_iap","model_location":"asia-northeast3","monthly_budget_usd":500,"max_llm_calls_per_turn":8}'

# Step 2: 사용자 환경 기준으로 scan & simulate 실행
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan . --target "$TARGET" --items "$ITEMS"
python3 "<SKILL_DIR>/scripts/prod_ready.py" simulate . --target "$TARGET" --items "$ITEMS"

# Step 3 & 4: 맞춤형 diff 검토 및 apply 적용
python3 "<SKILL_DIR>/scripts/prod_ready.py" diff . --target "$TARGET" --items "$ITEMS" --params "$PARAMS"
python3 "<SKILL_DIR>/scripts/prod_ready.py" apply . --target "$TARGET" --items "$ITEMS" --params "$PARAMS"

# 재검증 및 리포트 생성
uv lock
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan . --target "$TARGET" --items "$ITEMS"
python3 "<SKILL_DIR>/scripts/prod_ready.py" simulate . --target "$TARGET" --items "$ITEMS"
python3 "<SKILL_DIR>/scripts/prod_ready.py" report . --target "$TARGET" --items "$ITEMS" --format both -o READINESS_REPORT.md
```
