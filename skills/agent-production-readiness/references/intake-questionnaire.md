# Pre-Scan Workload Intake & Tailored Item Mapping (`intake-questionnaire.md`, v2 — 13 Core Items)

Before running `scan` or `simulate`, confirm the user's workload profile so irrelevant items are skipped (`N/A`) rather than raising false RED blockers.

---

## 1. Quick Presets (1-Step Selection)

| Preset | Description | Selected Items (`--items`) | Active Count |
| :--- | :--- | :--- | :---: |
| **1. 풀 프로덕션 (사내 DB/VPC 연동 포함)** | 멀티턴 · 사내 프라이빗 DB/VPC 연동 · SRE 알람 · CI/CD Eval 게이트 | `ALL` (`C1.1,C1.2,C2.1,C2.2,C3.1,C4.1,C4.3,C5.1,C5.2,C5.3,C6.1,C6.2,C7.1`) | **13 / 13** |
| **2. 표준 프로덕션 (외부/Public API 기반)** | 사내 프라이빗 DB 없이 Gemini 및 외부 공개 API만 호출하는 상용 멀티턴 에이전트 (`C3.1` 사내 VPC 제외) | `C1.1,C1.2,C2.1,C2.2,C4.1,C4.3,C5.1,C5.2,C5.3,C6.1,C6.2,C7.1` | **12 / 13** |
| **3. 경량 / PoC** | 단발성(Stateless) · 최소 비용 · 추가 인프라 없이 코드/컨테이너/프라이버시/루프 차단 기본선만 적용 | `C1.1,C1.2,C4.1,C4.3,C5.1,C5.2,C6.1,C6.2` | **8 / 13** |

---

## 2. Custom 3-Question Builder (When User Selects Option 4)

Always start from the **8 Common Baseline Items**:
`C1.1, C1.2, C4.1, C4.3, C5.1, C5.2, C6.1, C6.2`

Then union based on the 3 answers:
1. **Q1. 대화 기억 (Session & Memory)**:
   - `A) 단발성 질의응답 (Stateless)` → Skip `C2.1, C2.2` (and skip `SIM-4`)
   - `B) 멀티턴 대화 & 장기기억 (Managed Sessions & Memory Bank)` → Add **`C2.1, C2.2`**
2. **Q2. 사내 프라이빗 망 연동 (Private VPC Egress)**:
   - `A) Gemini / 외부 공개 API만 호출` → Skip `C3.1`
   - `B) 사내 프라이빗 DB · 내부 VPC 연동` → Add **`C3.1`**
3. **Q3. 운영 모니터링 & 배포 게이트 (SRE Alerts & CI/CD Eval Gate)**:
   - `A) PoC / 개발 테스트` → Skip `C5.3, C7.1`
   - `B) 실제 상용 운영 · SRE 알람 & CI/CD Eval 게이트` → Add **`C5.3, C7.1`**

---

## 3. Optional `--params` JSON Keys (Step 3 Tuning)

| Key | Default | Description |
| :--- | :--- | :--- |
| `agent_engine_id` | `""` | Agent Engine ID for Cloud Run Managed Sessions & Memory Bank (`C2.1, C2.2`) |
| `vpc_network_name` | `"default"` | Target VPC network name (`C3.1`) |
| `vpc_subnet_name` | `"default"` | Target VPC subnet name for Cloud Run Direct VPC Egress (`C3.1`) |
| `egress_mode` | `"PRIVATE_RANGES_ONLY"` | Cloud Run VPC Egress mode (`PRIVATE_RANGES_ONLY` or `ALL_TRAFFIC`) |
| `enable_bq_analytics` | `false` | Opt-in BigQuery Agent Analytics plugin in `US` multi-region (`C5.2`) |
| `latency_p95_ms` | `15000` | P95 latency alert threshold in ms (`C5.3`) |
| `max_llm_calls_per_turn` | `10` | Per-turn (`invocation_id`) LLM call cap (`C6.1`) |
| `max_output_tokens` | `0` | Opt-in per-request `max_output_tokens` cap (`0` = disabled for Thinking model safety, `C6.1`) |
| `model_location` | `"global"` | Gemini endpoint location (`global` for DSQ or regional for data residency, `C6.2`) |
| `eval_metrics` | `"final_response_quality,safety"` | Metrics passed to `agents-cli eval run` (`C7.1`) |
