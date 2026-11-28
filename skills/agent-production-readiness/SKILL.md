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

## Checklist v3 Items

ID format: `<target>-<category><n>` — target `AR` · `CR` · `GKE`; category `F` State & Memory · `O` Runtime Lifecycle · `D` CI/CD & Evaluation · `OB` Observability · `S` Security & Governance. ⚙️ = conditional, 🅿️ = Preview, 🧩 = Optional Add-on (Tier 3 · opt-in only, never blocks verdict).

| Category | 🛡️ Agent Runtime | ⚡ Cloud Run | ☸️ GKE |
| :--- | :--- | :--- | :--- |
| State & Memory | `AR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `AR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ | `CR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `CR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ | `GKE-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `GKE-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ |
| Runtime Lifecycle | — | — | `GKE-O1` 무중단 종료 (긴 응답 보호) · `GKE-O2` 에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox) ⚙️ |
| CI/CD & Evaluation | `AR-D1` CI/CD 파이프라인 (agents-cli) · `AR-D2` 평가 데이터셋 · 평가 게이트 | `CR-D1` CI/CD 파이프라인 (agents-cli) · `CR-D2` 평가 데이터셋 · 평가 게이트 | `GKE-D1` CI/CD 파이프라인 (agents-cli) · `GKE-D2` 평가 데이터셋 · 평가 게이트 |
| Observability | `AR-OB1` 프롬프트 미기록 · `AR-OB2` BigQuery Agent Analytics (구조화 이벤트 분석) 🧩 | `CR-OB1` Cloud Trace · 프롬프트 미기록 · `CR-OB2` BigQuery Agent Analytics (구조화 이벤트 분석) 🧩 | `GKE-OB1` Cloud Trace · 프롬프트 미기록 · `GKE-OB2` BigQuery Agent Analytics (구조화 이벤트 분석) 🧩 |
| Security & Governance | `AR-S1` 에이전트 전용 신원 · 최소 권한 · `AR-S2` Agent Registry 등록 🅿️ · `AR-S3` Agent Gateway (외부 호출 통제) ⚙️🅿️ · `AR-S4` AI 생성 코드 격리 (Code Execution) ⚙️ · `AR-S5` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️ · `AR-S6` Model Armor 입·출력 가드레일 (프롬프트 인젝션 · SDP) 🧩 · `AR-S7` 고위험 도구 실행 승인 게이트 (ADK Tool Confirmation · HITL) 🧩 | `CR-S1` 인증된 호출만 허용 · `CR-S2` 에이전트 전용 신원 · 최소 권한 · `CR-S3` Agent Registry 등록 🅿️ · `CR-S4` Agent Gateway (외부 호출 통제) ⚙️🅿️ · `CR-S5` AI 생성 코드 격리 (Cloud Run sandboxes) ⚙️🅿️ · `CR-S6` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️ · `CR-S7` Model Armor 입·출력 가드레일 (프롬프트 인젝션 · SDP) 🧩 · `CR-S8` 고위험 도구 실행 승인 게이트 (ADK Tool Confirmation · HITL) 🧩 | `GKE-S1` 에이전트 전용 신원 · 최소 권한 · `GKE-S2` AI 생성 코드 격리 (Agent Substrate · Agent Sandbox) ⚙️ · `GKE-S3` Agent Registry 등록 🅿️ · `GKE-S4` Agent Gateway for GKE (외부 호출 통제) ⚙️🅿️ · `GKE-S5` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️ · `GKE-S6` Model Armor 입·출력 가드레일 (프롬프트 인젝션 · SDP) 🧩 · `GKE-S7` 고위험 도구 실행 승인 게이트 (ADK Tool Confirmation · HITL) 🧩 |

Targets are listed Agent Runtime → Cloud Run → GKE (fully managed ↔ most control / ops responsibility). Agent Runtime and Cloud Run currently have no Runtime Lifecycle item; reports skip empty categories. `CR-S5` / `AR-S4` (AI-generated code isolation) and `AR-S7` / `CR-S8` / `GKE-S7` (ADK Tool Confirmation · HITL) are guidance-only: the engine checks them but never auto-patches. Item counts: Agent Runtime 13 · Cloud Run 14 · GKE 15 (42 total = 33 Core/Conditional + 9 Optional Add-ons).

**`idle` condition (`GKE-O2`)** cannot be detected from code: it counts as met only when Agent Substrate (`ate.dev/v1alpha1` `WorkerPool` / `ActorTemplate`) is already used. Otherwise ask the user ("사용자 입력·도구 응답을 기다리는 시간이 대부분인가?"); if yes, pass `--conditions idle=true` to `scan` / `report` / `apply`, or select `--items GKE-O2` explicitly. `GKE-O2` reuses the `GKE-S2` Agent Substrate manifest and needs GKE Standard >= 1.37 (the engine never changes an existing Autopilot cluster, so it stays 🟡 there).

**`delegated` condition (`AR-S5` · `CR-S6` · `GKE-S5`, 사용자 위임 인증 · Preview · guidance only)** — met when the agent calls external tools / MCP servers (Jira, GitHub, …) on behalf of users. Auto-detected from Agent Identity auth manager usage (`google.adk.integrations.agent_identity`, `GcpAuthProvider` / `GcpAuthProviderScheme`, `…/authProviders/…`), credential-bearing tool configs (MCP / OpenAPI / third-party API tools with auth headers) or secret-like env vars (`*_TOKEN`, `*_API_KEY`, `GITHUB_PAT`, …; Gemini keys excluded). If undetectable it is N/A — ask the user ("사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하는가?") and pass `--conditions delegated=true` if yes. Status: 🟢 `google-adk[agent-identity]` extra + auth provider wiring · 🔴 static API keys / PATs passed into tool headers · 🟡 otherwise. The engine never auto-patches it: guide the user to create an auth provider (3-legged OAuth) in Agent Identity auth manager and attach it to tools with `google-adk[agent-identity]` ([auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)).

Conditions (catalog `condition_rules`): **멀티턴 대화** → sessions (`*-F1`) · **장기 기억 필요** → Memory Bank (`*-F2`) · **AI 생성 코드 실행** → `AR-S4` · `CR-S5` · `GKE-S2` · **자주 일시정지·재개하는 상태 유지형 에이전트** (`idle`) → `GKE-O2` · **사용자 대신 외부 도구 호출** (`delegated`) → 사용자 위임 인증 (`AR-S5` · `CR-S6` · `GKE-S5`) · **MCP·외부 도구 호출** → Agent Gateway (`AR-S3` · `CR-S4` · `GKE-S4`).

**Legacy IDs**: earlier `C*` IDs (e.g. `C2.1`, `C4.1`, `C7.1`) are still accepted as aliases in `--items` and resolve to the target's v3 IDs. `C*` IDs for delegated BPs (`C1.1`, `C3.1`, `C4.3`, `C5.3`, and the probe/PDB part of `C1.2`) no longer map to checklist items — point the user to the platform skills instead. `C6.2` (Gemini call retry · endpoint) is ADK optimization (agent code) and out of scope: it resolves to nothing, the CLI says so, and you should not check, patch, or recommend it as part of this skill. Mapping: `references/checklist-rubric.md` §4.

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
  - **특정 항목 ID 직접 선택** (예: `CR-F1, CR-S1, CR-OB1` 또는 `AR-D2, AR-S3`)
  - **필수 미충족 항목만 순차 적용** (Preview 제외)
  - **지금은 진단만 확인하고 종료**
- For `*-D1` (CI/CD pipeline): `*-D1` is guidance-only (the engine never generates pipeline files). If selected while RED, propose running `agents-cli scaffold enhance . --cicd-runner google_cloud_build` and execute it **only after explicit user approval** (before `*-D2`). `agents-cli infra cicd` is always a manual production step — mention as guidance, never run it.

### Step 3: Feature-by-Feature Loop — 항목별 설정 질문 → Diff → 승인 후 Apply

Process the user's selected items **one feature at a time (1개 항목씩 순차 진행)** so the user stays in full control of every configuration decision and code/Terraform change. *(Only combine multiple items into one diff/apply if the user explicitly says "한 번에 적용해줘" or requests batch mode.)*

For each selected item `<ONE_ITEM>`:

#### 3-1. Configure (해당 항목에 필요한 설정만 질문)
Consult `references/intake-questionnaire.md` **§4 (선택 항목별 적용 전 설정 질문)**:
- **Skip questions** for single-answer items (`CR-S1`, `CR-OB1`, `AR-OB1`, `GKE-OB1`, `AR-S1`, `GKE-S1`, etc.) — go straight to 3-2 Diff.
- **Ask only the questions required by `<ONE_ITEM>`** (if not already answered by the user):
  - **Agent Gateway (`AR-S3` · `CR-S4` · `GKE-S4`)**: ① 적용 방향 — `Egress (에이전트→외부 도구/MCP)` / `Ingress (클라이언트→에이전트)` / `Both (양방향)` (`agent_gateway_direction`), ② 초기 적용 모드 — `DRY_RUN (감사 후 전환 권장)` vs `ENFORCED` (`agent_gateway_enforcement_mode`), ③ `Model Armor` 프롬프트·민감정보 검사 연동 여부 (`enable_model_armor`).
  - **Cloud Run 신원 · 레지스트리 (`CR-S2` · `CR-S3`)**: ① 신원 방식 — `전용 최소 권한 서비스 계정 (권장·안정)` vs `Agent Identity (Preview SPIFFE)` (`cloud_run_identity_mode`), ② `Agent Identity` 선택 시 조직 ID (`organization_id`, 미입력 시 `gcloud projects get-ancestors` 자동 조회 안내).
  - **평가 게이트 (`*-D2`)**: 통과 기준 점수 (`eval_threshold`, 기본 `3.5`).
  - **영속 세션 · 장기 기억 (`CR-F1/F2` · `GKE-F1/F2`)**: 기존 Agent Platform Reasoning Engine ID가 있는지 (`agent_engine_id`; 없으면 신규 생성 안내).
  - **GKE 무중단 종료 (`GKE-O1`)**: 최대 응답 시간에 맞춘 종료 유예 시간 (`gke_termination_grace_seconds`, 기본 `120`초).
  - **GKE 코드 격리 · 유휴 일시정지 (`GKE-S2` · `GKE-O2`)**: 실행 패턴 — `agent_substrate` (Standard ≥ 1.37 권장) vs `agent_sandbox` (상시 실행 · 사이드카 · Arm · Autopilot) (`gke_execution_pattern`).
  - **BigQuery Agent Analytics (`AR-OB2` · `CR-OB2` · `GKE-OB2`, 선택 Add-on)**: 데이터셋 ID (`bq_analytics_dataset`, 기본 `agent_analytics`), 테이블명 (`bq_analytics_table`, 기본 `agent_events`).
  - **Model Armor 가드레일 (`AR-S6` · `CR-S7` · `GKE-S6`, 선택 Add-on · 유료)**: ① 프롬프트 인젝션·탈옥 신뢰도 임계치 (`LOW_AND_ABOVE` / `MEDIUM_AND_ABOVE` / `HIGH`, `model_armor_confidence`), ② 민감정보 SDP 필터 (`model_armor_enable_sdp`), ③ 악성 URI 필터 (`model_armor_enable_malicious_uri`).
  - **고위험 도구 승인 게이트 · HITL (`AR-S7` · `CR-S8` · `GKE-S7`, 선택 Add-on · 안내 전용)**: 사람 승인을 걸 대상 고위험 도구(결제·삭제·외부 쓰기 등) 목록을 확인한 뒤 `FunctionTool(..., require_confirmation=True)` 및 `ToolConfirmation` 적용 코드 예시를 안내합니다.
  - **기타 안내 전용 항목 (`CR-S4`, `AR-S4`, `CR-S5`, `AR-S5`, `CR-S6`, `GKE-S5`)**: 사용자 응답에 맞춘 맞춤형 YAML / CLI / 코드 설정 가이드와 공식 근거 링크를 제공하고 다음 항목으로 이동합니다.

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
