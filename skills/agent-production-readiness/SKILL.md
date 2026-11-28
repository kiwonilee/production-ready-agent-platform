---
name: agent-production-readiness
description: >-
  Checks and fixes production readiness of local Google Cloud AI agent projects
  (ADK or google-agents-cli) on Cloud Run, Agent Runtime, or GKE using checklist v3:
  only items derived from agent characteristics (persistent sessions, Memory Bank,
  agents-cli CI/CD pipeline, eval dataset and score gate, Cloud Trace without prompt capture,
  authenticated invocation, dedicated agent identity, Agent Registry, Agent Gateway,
  user-delegated auth via Agent Identity auth manager,
  Agent Substrate on GKE for AI-generated code). Conditional items apply only when
  the condition is detected; Preview items are reported but never block the verdict.
  General platform best practices (probes, PDB, IaC, alerts, non-root, private
  networking, PSS) are delegated to the Google Cloud platform skills. Use when
  checking or improving production readiness of an ADK/agents-cli agent. Don't use
  for writing agent logic (use google-agents-cli-adk-code) or non-agent GKE admin
  (use gke-productionize).
license: Apache-2.0
compatibility: Works with Antigravity, Gemini CLI, Claude Code, Cursor, and google-agents-cli (v1.5.0+). Requires Python 3.10+.
metadata:
  author: Google Cloud
  version: 3.0.0
  category: Agent Platform & Production Readiness
  requires-skills: "google-agents-cli-deploy, google-agents-cli-eval, google-agents-cli-scaffold"
  recommends-skills: "agent-platform-deploy, agent-platform-agent-registry, agent-platform-access-audit, agent-platform-alert-configuration, cloud-run-basics, cloud-run-alert-configuration, google-cicd-terraform, google-cloud-waf-security, gke-productionize, gke-reliability, gke-workload-security, gke-workload-identity, gke-networking, gke-alert-configuration"
  requires-tools: "agents-cli>=1.5.0"
---

# AI Agent Production-Readiness Skill (`agent-production-readiness`, checklist v3)

This skill scans a local ADK / `google-agents-cli` agent project, reports production readiness against **checklist v3**, previews diffs, and applies fixes in place — separately for **Cloud Run (`cloud_run`)**, **Agent Runtime (`agent_runtime`)**, and **GKE (`gke`)**. Everything runs locally via the bundled stdlib-only engine `scripts/prod_ready.py`.

- Source of truth: `scripts/prod_ready/catalog_v3.json` (items, conditions, advisories, delegated platform BPs, platform skills). Item tables: `references/checklist-rubric.md`. Web view: `/checklist` (`?track=cloud_run|agent_runtime|gke`).

## Core Principle — Agent Characteristics Only

The checklist contains **only items that follow from agent characteristics**: "에이전트의 X 특성 때문에 Y를 해야 한다" (because the agent has trait X, it must do Y). Examples: conversation state → persistent session; LLM non-determinism → CI/CD eval gate; autonomous tool calls → dedicated identity and Agent Gateway; long streaming turns → graceful termination on GKE; untrusted AI-generated code → Agent Substrate sandbox.

> **IMPORTANT — always recommend the platform skills.** General platform best practices that apply to *any* container service (health probes, multi-zone/PDB, Terraform IaC, latency/error alerts, Secret Manager pinning, non-root containers, Direct VPC Egress / PSC Interface, Private Nodes / NetworkPolicy, Pod Security Standards, no service-account JSON keys / Workload Identity Federation) are **not checked** here. In every scan summary and report, **strongly recommend that the user also run the Google Cloud platform skills** at https://github.com/google/skills/tree/main/skills/cloud for the target (catalog `platform_skills`; delegated BP→skill table in catalog `delegated_platform_bps`, rendered in `references/checklist-rubric.md` §5). Passing this checklist does **not** replace those checks.

| Target | Platform skills to also consult |
| :--- | :--- |
| `agent_runtime` | `agent-platform-deploy` · `agent-platform-agent-registry` · `agent-platform-access-audit` · `agent-platform-alert-configuration` |
| `cloud_run` | `cloud-run-basics` · `cloud-run-alert-configuration` · `agent-platform-alert-configuration` · `google-cicd-terraform` · `google-cloud-waf-security` |
| `gke` | `gke-productionize` · `gke-reliability` · `gke-workload-security` · `gke-workload-identity` · `gke-networking` · `gke-alert-configuration` · `agent-platform-alert-configuration` |

Values (instance counts, timeouts, sizing), architecture choices, and non-universal infrastructure options (Secure Web Proxy, Cloud Armor, Provisioned Throughput) are **advisories** only (catalog `advisories`) — mention them when relevant, never as checklist failures.

## Checklist Items (v4 — 4 Balanced Categories · Common + Runtime-Specific)

ID format: `<scope>-<category><n>` — scope `COMMON` (all runtimes) · `AR` (Agent Runtime) · `CR` (Cloud Run) · `GKE`; 4 balanced categories: **`RS`** Runtime & State · **`ID`** Identity & Governance · **`SF`** Safety & Guardrails · **`DO`** Delivery, Eval & Observability. ⚙️ = conditional, 🅿️ = Preview, 🧩 = Optional Add-on (Tier 3 · opt-in only, never blocks verdict).

### 1) 🌐 Common Across All Runtimes (`COMMON-*`, 6 items)
- `COMMON-DO1` CI/CD 파이프라인 (agents-cli) · `COMMON-DO2` 평가 데이터셋 · 평가 게이트 · `COMMON-DO3` BigQuery Agent Analytics (구조화 이벤트 분석) 🧩
- `COMMON-ID1` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️
- `COMMON-SF1` Model Armor 입·출력 가드레일 (프롬프트 인젝션 · SDP) 🧩 · `COMMON-SF2` 고위험 도구 실행 승인 게이트 (ADK Tool Confirmation · HITL) 🧩

### 2) ⚙️ Runtime-Specific Items (25 items — tailored per platform, zero duplication)

| Category | 🛡️ Agent Runtime (`AR-*`, 8) | ⚡ Cloud Run (`CR-*`, 8) | ☸️ GKE (`GKE-*`, 9) |
| :--- | :--- | :--- | :--- |
| **RS · Runtime & State** | `AR-RS1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `AR-RS2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ | `CR-RS1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `CR-RS2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ | `GKE-RS1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `GKE-RS2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ · `GKE-RS3` 무중단 종료 (긴 응답 보호) · `GKE-RS4` 에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox) ⚙️ |
| **ID · Identity & Governance** | `AR-ID1` 에이전트 전용 신원 · 최소 권한 · `AR-ID2` Agent Registry 등록 🅿️ | `CR-ID1` 인증된 호출만 허용 · `CR-ID2` 에이전트 전용 신원 · 최소 권한 · `CR-ID3` Agent Registry 등록 🅿️ | `GKE-ID1` 에이전트 전용 신원 · 최소 권한 · `GKE-ID2` Agent Registry 등록 🅿️ |
| **SF · Safety & Guardrails** | `AR-SF1` Agent Gateway (외부 호출 통제) ⚙️🅿️ · `AR-SF2` AI 생성 코드 격리 (Code Execution) ⚙️ | `CR-SF1` Agent Gateway (외부 호출 통제) ⚙️🅿️ · `CR-SF2` AI 생성 코드 격리 (Cloud Run sandboxes) ⚙️🅿️ | `GKE-SF1` AI 생성 코드 격리 (Agent Substrate · Agent Sandbox) ⚙️ · `GKE-SF2` Agent Gateway for GKE (외부 호출 통제) ⚙️🅿️ |
| **DO · Delivery, Eval & Observability** | `AR-DO1` 프롬프트 미기록 · `AR-DO2` Online Evaluation Monitor (실시간 품질 감시) 🧩 | `CR-DO1` Cloud Trace · 프롬프트 미기록 | `GKE-DO1` Cloud Trace · 프롬프트 미기록 |

Total unique catalog items: **31** (`COMMON` 6 + `AR` 8 + `CR` 8 + `GKE` 9). When scanning a target, the engine evaluates **that target's runtime-specific items + the 6 `COMMON-*` items**. Guidance-only items: `COMMON-DO1`, `COMMON-ID1`, `COMMON-SF2`, `AR-SF2`, `AR-DO2`, `CR-SF1`, `CR-SF2`.

**`idle` condition (`GKE-RS4`)** cannot be detected from code: it counts as met only when Agent Substrate (`ate.dev/v1alpha1` `WorkerPool` / `ActorTemplate`) is already used. Otherwise ask the user ("사용자 입력·도구 응답을 기다리는 시간이 대부분인가?"); if yes, pass `--conditions idle=true` to `scan` / `report` / `apply`, or select `--items GKE-RS4` explicitly. `GKE-RS4` reuses the `GKE-SF1` Agent Substrate manifest and needs GKE Standard >= 1.37 (the engine never changes an existing Autopilot cluster, so it stays 🟡 there).

**`delegated` condition (`COMMON-ID1`, 사용자 위임 인증 · Preview · guidance only)** — met when the agent calls external tools / MCP servers (Jira, GitHub, …) on behalf of users. Auto-detected from Agent Identity auth manager usage (`google.adk.integrations.agent_identity`, `GcpAuthProvider` / `GcpAuthProviderScheme`, `…/authProviders/…`), credential-bearing tool configs (MCP / OpenAPI / third-party API tools with auth headers) or secret-like env vars (`*_TOKEN`, `*_API_KEY`, `GITHUB_PAT`, …; Gemini keys excluded). If undetectable it is N/A — ask the user ("사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하는가?") and pass `--conditions delegated=true` if yes. Status: 🟢 `google-adk[agent-identity]` extra + auth provider wiring · 🔴 static API keys / PATs passed into tool headers · 🟡 otherwise.

Conditions (catalog `condition_rules`): **멀티턴 대화** → sessions (`*-RS1`) · **장기 기억 필요** → Memory Bank (`*-RS2`) · **AI 생성 코드 실행** → `AR-SF2` · `CR-SF2` · `GKE-SF1` · **자주 일시정지·재개하는 상태 유지형 에이전트** (`idle`) → `GKE-RS4` · **사용자 대신 외부 도구 호출** (`delegated`) → `COMMON-ID1` · **MCP·외부 도구 호출** → Agent Gateway (`AR-SF1` · `CR-SF1` · `GKE-SF2`).

**Legacy IDs**: Previous v3 IDs (e.g. `CR-F1`, `AR-S3`, `GKE-O1`, `CR-D2`) AND v2 `C*` IDs (e.g. `C2.1`, `C4.1`, `C7.1`) are transparently accepted as aliases in `--items` and resolve to the canonical v4 IDs (`CR-RS1`, `AR-SF1`, `GKE-RS3`, `COMMON-DO2`). Delegated BPs (`C1.1`, `C3.1`, `C4.3`, `C5.3`) point to platform skills; `C6.2` is out of scope. Mapping: `references/checklist-rubric.md` §4.

### Verdict Rules
- **Required** items must be `GREEN` for 🟢 Production Ready.
- **Conditional** items are evaluated only when the condition applies; otherwise `N/A` (excluded).
- **Preview** (`🅿️`) and **Optional Add-on** (`🧩`, Tier 3) items are reported for visibility and **never block** Production Ready, and are **never auto-applied on `ALL`** unless explicitly selected by the user.
- 🟢 Production Ready = all evaluated blocking items GREEN · 🟡 Ready with Follow-ups = no RED, ≥1 AMBER · 🔴 Not Production Ready = ≥1 RED.

---

## Reference Manuals (Progressive Disclosure)

Load from `references/` (relative to this `SKILL.md`) only when needed:

- **`references/intake-questionnaire.md`** — **READ FIRST before `scan`**: condition auto-detection signals, the minimal questions to ask, advisory follow-ups, optional `--params`.
- **`references/checklist-rubric.md`** — Per-target v3 item tables (ID, category, title, grade/condition, Preview, Check, Best Practice, references), verdict rules, legacy alias map, delegated BP → platform skill table, advisories.
- **`references/cloud-run-hardening.md`** — Cloud Run production guide for `CR-*` items (code/Terraform snippets).
- **`references/agent-runtime-hardening.md`** — Agent Runtime production guide for `AR-*` items (keep managed defaults, Agent Identity, Agent Gateway `DRY_RUN` → `ENFORCED`).
- **`references/gke-hardening.md`** — GKE production guide for `GKE-*` items (graceful termination, Agent Substrate vs. Agent Sandbox, Agent Gateway for GKE).
- **`references/agent-registry-mcp-a2a.md`** — **Conditional**: load only when MCP servers, MCP/A2A URLs, or Agent Registry/Gateway integration are detected or requested.
- **`references/deployment-guide.md`** — Install, scan/apply/report commands, and deployment (`terraform`, `agents-cli deploy`, `kubectl`).
- **`assets/terraform/`**, **`assets/python/`** — Reference drop-in modules (`services.py` session helper).

---

## Workflow

```text
0. 의존성 점검 (doctor)
  ──▶ 1. Find: 전체 진단 · 항목별 1줄 설명 리포트 (수정 없음)
  ──▶ 2. Select: 적용할 항목 선택 (임의 일괄 적용 금지)
  ──▶ 3. Feature-by-Feature: 항목별 설정 질문 ──▶ Diff 확인 ──▶ 승인 후 Apply (1개씩 순차 진행)
  ──▶ 4. 최종 재검사 · uv lock · 감사 리포트 (READINESS_REPORT)
  ──▶ 5. 배포 · 라이브 검증 · 플랫폼 Skill 점검
```

Let `<SKILL_DIR>` be the directory containing this `SKILL.md`. Always run `<SKILL_DIR>/scripts/prod_ready.py` with `python3` (pure stdlib).

> **Core Interactive Rule (Option 1 — Select-and-Configure Feature-by-Feature)**:
> Every production environment is different. **Never** run `apply --items ALL` blindly without user selection. Always: (1) **Find** and explain every item in 1 plain Korean line, (2) let the user **Select** which specific item IDs they want to adopt, and (3) process **one selected feature at a time** — asking only the configuration questions needed for *that* item, showing its exact `diff`, and running `apply` only after explicit user approval. *(Only batch multiple items into a single diff/apply if the user explicitly asks to apply several items together, e.g. in a demo or lab.)*

### Step 0: 의존성 점검 (Dependency Preflight — ALWAYS run first, even if the user prompt does not mention `doctor`)

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" doctor <project_dir> --target <agent_runtime|cloud_run|gke>
```

- **MANDATORY FIRST STEP**: Always execute `prod_ready.py doctor` BEFORE `scan` (even when the user simply asks to "점검해줘" or "진단 결과만 표로 보여줘") and **always include a concise `🩺 의존성 점검 (doctor)` summary at the top of your response**.
- **Required** (exit 1 if missing): `agents-cli` >= 1.5.0 and the agents-cli skills `google-agents-cli-deploy` · `google-agents-cli-eval` · `google-agents-cli-scaffold`. If anything is missing, **stop** and ask the user to approve installing it: `uv tool install google-agents-cli` (or `uv tool upgrade google-agents-cli`) for the binary, then `agents-cli setup` for the skills. Re-run `doctor` after installation. Do not continue to Step 1 until the required items are present (`--skip-deps-check` exists only for CI/emergencies — never use it silently).
- **Recommended** (never blocking): the Google Cloud platform skills for the target (catalog `platform_skills`, https://github.com/google/skills/tree/main/skills/cloud). If any are missing, **strongly recommend** installing them and ask the user for approval to run `python3 "<SKILL_DIR>/scripts/prod_ready.py" doctor <project_dir> --target <t> --install-platform-skills` (or `npx skills add google/skills --skill <name> ... -g -y`). Never install without the user's explicit consent. Proceed to Step 1 after the user answers either way.
- As a safety net, `scan` / `diff` / `apply` / `report` also enforce the required-dependency gate and print the `Doctor Preflight` summary (and a warning banner if platform skills are missing), **but never skip Step 0 `doctor` or omit its summary from your response**.

### Step 1: Find — 대상 환경 판별 · 전체 진단 · 항목별 1줄 설명 (수정 없음)

Follow `references/intake-questionnaire.md` (§1–§3):
1. Determine the target (`cloud_run` · `agent_runtime` · `gke`) from the prompt or workspace (or use `--target auto`).
2. **Auto-detect the 6 conditions from code first** (`conv`, `memory`, `code`, `tools`, `delegated`, `idle`). Tell the user the detected result in one line each; ask only about conditions you could not detect (yes/no, all at once). Never ask about required items, and do not offer "full / PoC" presets.
3. Run a read-only full Best Practice scan (never modify files during Step 1):

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <project_dir> --target <target> --items ALL
```

Present the **Find Report** in Korean, in this order:
1. **`🩺 의존성 점검 (doctor)` summary**: `agents-cli` version, required `google-agents-cli-*` skills, and target Google Cloud platform skills (✅/❌).
2. **Gate verdict** and detected conditions.
3. **체크리스트 진단표 (1부: 필수 · 조건부 핵심 판정 / 2부: 선택형 Add-on 카탈로그, 빈 범주 생략)**: For **every item** (so the user can decide with evidence), ALWAYS include:
   - **ID & 항목명** + grade badge (`필수` / `조건부 ⚙️` / `Preview 🅿️` / `선택 Add-on 🧩`)
   - **현재 상태** (`🟢`/`🟡`/`🔴`/`⚪ N/A` 또는 `⚪ 선택 미적용`)
   - **항목 1줄 설명** — plain Korean explanation of *what this item does and why an agent needs it*
   - **공식 근거 (`references`)** — ALWAYS include the clickable Google Cloud / ADK / Kubernetes official doc link(s) from `catalog_v3.json` for **every item** so every recommendation is visibly grounded
   - **적용 방식** — `자동 패치` vs `안내 전용 (수동)`
   - For non-green items: brief **Check** & **Best Practice**. Clearly note that Preview (`🅿️`) and Optional Add-on (`🧩`) items never block the verdict.
4. **Platform skill recommendation** for the target (table above) — always include it.
5. **Advisories** relevant to the user's situation (optional).

If MCP servers or hardcoded MCP/A2A URLs are detected, load `references/agent-registry-mcp-a2a.md` and note the `AgentRegistry.get_mcp_toolset()` / `get_remote_a2a_agent()` migration recipe.

### Step 2: Select — 사용자가 적용할 항목 선택 (임의 일괄 적용 금지)

Right after presenting the Step 1 Find Report, **stop and ask the user which items they actually want to apply** — never assume all RED/AMBER items should be applied:
- Offer clear choices (using `ask_question` if available, or concise plain-text options):
  - **특정 항목 ID 직접 선택** (예: `CR-RS1, CR-ID1, CR-DO1` 또는 `AR-SF1, COMMON-SF1` — 기존 v3 ID 별칭도 자동 해석)
  - **필수 미충족 항목만 순차 적용** (Preview · 선택 Add-on 제외)
  - **지금은 진단만 확인하고 종료**
- For `COMMON-DO1` (CI/CD pipeline): `COMMON-DO1` is guidance-only (the engine never generates pipeline files). If selected while RED, propose running `agents-cli scaffold enhance . --cicd-runner google_cloud_build` and execute it **only after explicit user approval** (before `COMMON-DO2`). `agents-cli infra cicd` is always a manual production step — mention as guidance, never run it.

### Step 3: Feature-by-Feature Loop — 항목별 설정 질문 → Diff → 승인 후 Apply

Process the user's selected items **one feature at a time (1개 항목씩 순차 진행)** so the user stays in full control of every configuration decision and code/Terraform change. *(Only combine multiple items into one diff/apply if the user explicitly says "한 번에 적용해줘" or requests batch mode.)*

For each selected item `<ONE_ITEM>`:

#### 3-1. Configure (해당 항목에 필요한 설정만 질문)
Consult `references/intake-questionnaire.md` **§4 (선택 항목별 적용 전 설정 질문)**:
- **Skip questions** for single-answer items (`CR-ID1`, `CR-DO1`, `AR-DO1`, `GKE-DO1`, `AR-ID1`, `GKE-ID1`, etc.) — go straight to 3-2 Diff.
- **Ask only the questions required by `<ONE_ITEM>`** (if not already answered by the user):
  - **Agent Gateway (`AR-SF1` · `CR-SF1` · `GKE-SF2`)**: ① 적용 방향 — `Egress (에이전트→외부 도구/MCP)` / `Ingress (클라이언트→에이전트)` / `Both (양방향)` (`agent_gateway_direction`), ② 초기 적용 모드 — `DRY_RUN (감사 후 전환 권장)` vs `ENFORCED` (`agent_gateway_enforcement_mode`), ③ `Model Armor` 프롬프트·민감정보 검사 연동 여부 (`enable_model_armor`).
  - **Cloud Run 신원 · 레지스트리 (`CR-ID2` · `CR-ID3`)**: ① 신원 방식 — `전용 최소 권한 서비스 계정 (권장·안정)` vs `Agent Identity (Preview SPIFFE)` (`cloud_run_identity_mode`), ② `Agent Identity` 선택 시 조직 ID (`organization_id`, 미입력 시 `gcloud projects get-ancestors` 자동 조회 안내).
  - **평가 게이트 (`COMMON-DO2`)**: 통과 기준 점수 (`eval_threshold`, 기본 `3.5`).
  - **영속 세션 · 장기 기억 (`CR-RS1/RS2` · `GKE-RS1/RS2`)**: 기존 Agent Platform Reasoning Engine ID가 있는지 (`agent_engine_id`; 없으면 신규 생성 안내).
  - **GKE 무중단 종료 (`GKE-RS3`)**: 최대 응답 시간에 맞춘 종료 유예 시간 (`gke_termination_grace_seconds`, 기본 `120`초).
  - **GKE 코드 격리 · 유휴 일시정지 (`GKE-SF1` · `GKE-RS4`)**: 실행 패턴 — `agent_substrate` (Standard ≥ 1.37 권장) vs `agent_sandbox` (상시 실행 · 사이드카 · Arm · Autopilot) (`gke_execution_pattern`).
  - **BigQuery Agent Analytics (`COMMON-DO3`, 선택 Add-on)**: 데이터셋 ID (`bq_analytics_dataset`, 기본 `agent_analytics`), 테이블명 (`bq_analytics_table`, 기본 `agent_events`).
  - **Model Armor 가드레일 (`COMMON-SF1`, 선택 Add-on · 유료)**: ① 프롬프트 인젝션·탈옥 신뢰도 임계치 (`LOW_AND_ABOVE` / `MEDIUM_AND_ABOVE` / `HIGH`, `model_armor_confidence`), ② 민감정보 SDP 필터 (`model_armor_enable_sdp`), ③ 악성 URI 필터 (`model_armor_enable_malicious_uri`).
  - **고위험 도구 승인 게이트 · HITL (`COMMON-SF2`, 선택 Add-on · 안내 전용)**: 사람 승인을 걸 대상 고위험 도구(결제·삭제·외부 쓰기 등) 목록을 확인한 뒤 `FunctionTool(..., require_confirmation=True)` 및 `ToolConfirmation` 적용 코드 예시를 안내합니다.
  - **Agent Runtime Online Evaluation Monitor (`AR-DO2`, 선택 Add-on · 안내 전용)**: `agent-platform-alert-configuration` 스킬과 연계해 추가 LLM 평가 과금 동의 후 Online Monitor 프로비저닝을 안내합니다.
  - **기타 안내 전용 항목 (`CR-SF1`, `AR-SF2`, `CR-SF2`, `COMMON-ID1`)**: 사용자 응답에 맞춘 맞춤형 YAML / CLI / 코드 설정 가이드와 공식 근거 링크를 제공하고 다음 항목으로 이동합니다.

#### 3-2. Preview Diff (해당 항목 단일 Diff 미리보기 — 파일 수정 없음)
```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" diff <project_dir> --target <target> --items <ONE_ITEM> [--params '<json>']
```
- Summarize the exact patched/new files for `<ONE_ITEM>`.
- **Existing User Code Modification Policy (Mandatory — enforced by the skill, never needs to be repeated in user prompts)**:
  - Never modify files during `diff`.
  - When an item modifies existing application code (e.g., `*-F2` Memory Bank wiring `preload_memory` + `after_agent_callback(add_session_to_memory)` in `app/agent.py`, or `memory_service` in `app/fast_api_app.py`), the engine **never deletes original lines** — it comments out the original code with `# [ORIGINAL before prod-ready ...]` and wraps added/updated regions with `# --- [BEGIN prod-ready: ...] ---` / `# --- [END prod-ready: ...] ---`.
  - Explicitly call out any existing user-code files (`app/agent.py`, `app/fast_api_app.py`) in the diff summary and **wait for explicit user approval before running `apply`**. Never modify existing user code silently.
- The engine preserves existing sizing (CPU/memory/instances/replicas), cluster mode, ingress, and `agents-cli` resource names, and does **not** auto-apply advisories or delegated platform BPs.

#### 3-3. Apply (승인 후 해당 항목만 반영)
Only after the user approves `<ONE_ITEM>`'s diff:
```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" apply <project_dir> --target <target> --items <ONE_ITEM> [--params '<json>']
```
Report what was applied for `<ONE_ITEM>` and ask whether to proceed to the next selected item (or adjust/stop).

### Step 4: 최종 재검사 · `uv lock` · 감사 리포트

Once all user-selected items are applied (using the union of applied `<selected_items>` or `ALL` for full visibility):
```bash
uv lock   # if pyproject.toml exists and changed
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <project_dir> --target <target> --items ALL
python3 "<SKILL_DIR>/scripts/prod_ready.py" report <project_dir> --target <target> --items ALL --format both -o <project_dir>/READINESS_REPORT.md
```

After generating `READINESS_REPORT.md` and `READINESS_REPORT.html`, summarize:
1. Items applied by the user vs. items intentionally deferred/skipped,
2. The final gate verdict and any remaining Preview items or advisories,
3. The recommended Google Cloud platform skills for the target.

### Step 5: Deploy, Live Verification & Platform Skill Follow-up

Use `references/deployment-guide.md` for `terraform apply` (if the user does not specify a literal project ID, resolve it via `gcloud config get-value project`; prefer `deployment/terraform/single-project` when present; persist `project_id`, `region`, `organization_id` on Cloud Run, and `agent_engine_id` for `CR-F1/F2` or `GKE-F1/F2` in `terraform.tfvars` rather than transient `-var` flags so `agents-cli deploy` on GKE never resets `AGENT_ENGINE_SESSION_ID` to `""`), `agents-cli deploy` (only after summarizing commands and receiving explicit user approval; `--agent-identity` and `--service-account` are mutually exclusive), and GKE `kubectl apply` of generated manifests. If a Cloud Run `IDENTITY_TYPE_AGENT_IDENTITY` (Preview) revision returns `401 UNAUTHENTICATED` (`Workload certificate refresh failed`) when calling `VertexAiSessionService`, switch `service.tf` to the dedicated least-privilege `service_account = google_service_account.app_sa.email` (keeping `CR-S2` GREEN).

When the user requests **post-deploy live verification** (even briefly, e.g. "체크리스트 적용 항목대로 잘 동작하는지 검증해줘"), automatically inspect `READINESS_REPORT.md` / the applied checklist items and run **all applicable end-to-end behavioral checks without requiring the user to list them**, then summarize results in a Korean table:
- **Auth / Security**: Cloud Run unauthenticated call returns `403` vs IAM Identity Token call returns `200` (`CR-S1`); verify live Revision / Cloud Run v2 REST API (`functionalType`) / Reasoning Engine (`spec.identityType`, `spec.deploymentSpec.agentGatewayConfig`, `spec.deploymentSpec.env`) / Deployment identity (`Agent Identity` / dedicated SA), `NO_CONTENT`, `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`, `AGENT_ENGINE_SESSION_ID`, and `gcloud agent-registry agents list`.
- **Sessions & Memory Bank**: Run 3-turn `agents-cli run --url ... --mode=adk` — Turn 1 introduces a user fact (capture `Session: <id>`), Turn 2 resumes with `--session-id` (`*-F1`), Turn 3 starts a fresh session after a brief pause to test Memory Bank recall (`*-F2`).
- **Evaluation & Trace**: Run `agents-cli eval run --config tests/eval/eval_config.yaml --dataset tests/eval/datasets/basic-dataset.json` (with `--url` on Cloud Run / GKE port-forward) and provide the Cloud Trace Explorer link (`https://console.cloud.google.com/traces/explorer?project=<PROJECT_ID>`) to confirm prompts are not logged (`NO_CONTENT`).

Finish by recommending the target's platform skills from https://github.com/google/skills/tree/main/skills/cloud; when the user asks to follow up on general infrastructure best practices, automatically consult the installed platform skills for the target (probes, alerts, non-root/PSS, networking, IAM/WAF, IaC) without requiring the user to list those topics. When asked to clean up resources, always preview what will be destroyed — removing GKE `deployment/k8s/` first to avoid LB/Gateway finalizer hangs, force-deleting Agent Runtime engines that hold child sessions/memories via `DELETE .../reasoningEngines/<ID>?force=true`, and emptying non-empty `logs_data_bucket` objects — and wait for explicit approval before `terraform destroy`.

### Optional: Web Workbench

The skill has no web dependencies. For a browser UI use the repository's `web/` app (`uv run prod-ready ui <project_dir> --port 8090`), which also serves `/checklist`.
