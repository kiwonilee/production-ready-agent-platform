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
| `cloud_run` | `cloud-run-basics` · `cloud-run-alert-configuration` · `google-cicd-terraform` · `google-cloud-waf-security` |
| `gke` | `gke-productionize` · `gke-reliability` · `gke-workload-security` · `gke-workload-identity` · `gke-networking` · `gke-alert-configuration` |

Values (instance counts, timeouts, sizing), architecture choices, paid optional products (Model Armor, Cloud Armor, Secure Web Proxy, Provisioned Throughput) and app-design concerns are **advisories** only (catalog `advisories`) — mention them when relevant, never as checklist failures.

## Checklist v3 Items

ID format: `<target>-<category><n>` — target `AR` · `CR` · `GKE`; category `F` Features · `O` Optimize · `D` Delivery & Eval · `OB` Observability · `S` Security. ⚙️ = conditional, 🅿️ = Preview.

| Category | 🛡️ Agent Runtime | ⚡ Cloud Run | ☸️ GKE |
| :--- | :--- | :--- | :--- |
| Features | `AR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `AR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ | `CR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `CR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ | `GKE-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `GKE-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️ |
| Optimize | — | — | `GKE-O1` 무중단 종료 (긴 응답 보호) · `GKE-O2` 에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox) ⚙️ |
| Delivery & Eval | `AR-D1` CI/CD 파이프라인 (agents-cli) · `AR-D2` 평가 데이터셋 · 평가 게이트 | `CR-D1` CI/CD 파이프라인 (agents-cli) · `CR-D2` 평가 데이터셋 · 평가 게이트 | `GKE-D1` CI/CD 파이프라인 (agents-cli) · `GKE-D2` 평가 데이터셋 · 평가 게이트 |
| Observability | `AR-OB1` 프롬프트 미기록 | `CR-OB1` Cloud Trace · 프롬프트 미기록 | `GKE-OB1` Cloud Trace · 프롬프트 미기록 |
| Security | `AR-S1` 에이전트 전용 신원 · 최소 권한 · `AR-S2` Agent Registry 등록 🅿️ · `AR-S3` Agent Gateway (외부 호출 통제) ⚙️🅿️ · `AR-S4` AI 생성 코드 격리 (Code Execution) ⚙️ · `AR-S5` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️ | `CR-S1` 인증된 호출만 허용 · `CR-S2` 에이전트 전용 신원 · 최소 권한 · `CR-S3` Agent Registry 등록 🅿️ · `CR-S4` Agent Gateway (외부 호출 통제) ⚙️🅿️ · `CR-S5` AI 생성 코드 격리 (Cloud Run sandboxes) ⚙️🅿️ · `CR-S6` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️ | `GKE-S1` 에이전트 전용 신원 · 최소 권한 · `GKE-S2` AI 생성 코드 격리 (Agent Substrate · Agent Sandbox) ⚙️ · `GKE-S3` Agent Registry 등록 🅿️ · `GKE-S4` Agent Gateway for GKE (외부 호출 통제) ⚙️🅿️ · `GKE-S5` 사용자 위임 인증 (Agent Identity auth manager) ⚙️🅿️ |

Targets are listed Agent Runtime → Cloud Run → GKE (fully managed ↔ most control / ops responsibility). Agent Runtime and Cloud Run currently have no Optimize item; reports skip empty categories. `CR-S5` / `AR-S4` (AI-generated code isolation) are guidance-only: the engine checks them but never auto-patches (enable `--sandbox-launcher` / route the code executor to Agent Platform Code Execution yourself). Item counts: Agent Runtime 10 · Cloud Run 11 · GKE 12 (33 total).

**`idle` condition (`GKE-O2`)** cannot be detected from code: it counts as met only when Agent Substrate (`ate.dev/v1alpha1` `WorkerPool` / `ActorTemplate`) is already used. Otherwise ask the user ("사용자 입력·도구 응답을 기다리는 시간이 대부분인가?"); if yes, pass `--conditions idle=true` to `scan` / `report` / `apply`, or select `--items GKE-O2` explicitly. `GKE-O2` reuses the `GKE-S2` Agent Substrate manifest and needs GKE Standard >= 1.37 (the engine never changes an existing Autopilot cluster, so it stays 🟡 there).

**`delegated` condition (`AR-S5` · `CR-S6` · `GKE-S5`, 사용자 위임 인증 · Preview · guidance only)** — met when the agent calls external tools / MCP servers (Jira, GitHub, …) on behalf of users. Auto-detected from Agent Identity auth manager usage (`google.adk.integrations.agent_identity`, `GcpAuthProvider` / `GcpAuthProviderScheme`, `…/authProviders/…`), credential-bearing tool configs (MCP / OpenAPI / third-party API tools with auth headers) or secret-like env vars (`*_TOKEN`, `*_API_KEY`, `GITHUB_PAT`, …; Gemini keys excluded). If undetectable it is N/A — ask the user ("사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하는가?") and pass `--conditions delegated=true` if yes. Status: 🟢 `google-adk[agent-identity]` extra + auth provider wiring · 🔴 static API keys / PATs passed into tool headers · 🟡 otherwise. The engine never auto-patches it: guide the user to create an auth provider (3-legged OAuth) in Agent Identity auth manager and attach it to tools with `google-adk[agent-identity]` ([auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)).

Conditions (catalog `condition_rules`): **멀티턴 대화** → sessions (`*-F1`) · **장기 기억 필요** → Memory Bank (`*-F2`) · **AI 생성 코드 실행** → `AR-S4` · `CR-S5` · `GKE-S2` · **자주 일시정지·재개하는 상태 유지형 에이전트** (`idle`) → `GKE-O2` · **사용자 대신 외부 도구 호출** (`delegated`) → 사용자 위임 인증 (`AR-S5` · `CR-S6` · `GKE-S5`) · **MCP·외부 도구 호출** → Agent Gateway (`AR-S3` · `CR-S4` · `GKE-S4`).

**Legacy IDs**: earlier `C*` IDs (e.g. `C2.1`, `C4.1`, `C7.1`) are still accepted as aliases in `--items` and resolve to the target's v3 IDs. `C*` IDs for delegated BPs (`C1.1`, `C3.1`, `C4.3`, `C5.3`, and the probe/PDB part of `C1.2`) no longer map to checklist items — point the user to the platform skills instead. `C6.2` (Gemini call retry · endpoint) is ADK optimization (agent code) and out of scope: it resolves to nothing, the CLI says so, and you should not check, patch, or recommend it as part of this skill. Mapping: `references/checklist-rubric.md` §4.

### Verdict Rules
- **Required** items must be `GREEN` for 🟢 Production Ready.
- **Conditional** items are evaluated only when the condition applies; otherwise `N/A` (excluded).
- **Preview** items are checked and reported but **never block** Production Ready.
- 🟢 Production Ready = all evaluated non-Preview items GREEN · 🟡 Ready with Follow-ups = no RED, ≥1 AMBER · 🔴 Not Production Ready = ≥1 RED.

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
0. 의존성 점검 ──▶ 1. 조건 판단 ──▶ 2. Scan (정적 점검) ──▶ 3. Diff 미리보기 ──▶ 4. Apply · 재점검 · 리포트 ──▶ 5. 배포 · 플랫폼 Skill 점검
```

Let `<SKILL_DIR>` be the directory containing this `SKILL.md`. Always run `<SKILL_DIR>/scripts/prod_ready.py` with `python3` (pure stdlib).

### Step 0: 의존성 점검 (Dependency Preflight — mandatory)

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" doctor <project_dir> --target <agent_runtime|cloud_run|gke>
```

- **Required** (exit 1 if missing): `agents-cli` >= 1.5.0 and the agents-cli skills `google-agents-cli-deploy` · `google-agents-cli-eval` · `google-agents-cli-scaffold`. If anything is missing, **stop** and ask the user to approve installing it: `uv tool install google-agents-cli` (or `uv tool upgrade google-agents-cli`) for the binary, then `agents-cli setup` for the skills. Re-run `doctor` after installation. Do not continue to Step 1 until the required items are present (`--skip-deps-check` exists only for CI/emergencies — never use it silently).
- **Recommended** (never blocking): the Google Cloud platform skills for the target (catalog `platform_skills`, https://github.com/google/skills/tree/main/skills/cloud). If any are missing, **strongly recommend** installing them and ask the user for approval to run `python3 "<SKILL_DIR>/scripts/prod_ready.py" doctor <project_dir> --target <t> --install-platform-skills` (or `npx skills add google/skills --skill <name> ... -g -y`). Never install without the user's explicit consent. Proceed to Step 1 after the user answers either way.
- `scan` / `diff` / `apply` / `report` re-run this preflight automatically: they stop when required dependencies are missing and print a prominent "⚠️ 플랫폼 Skill 미설치 — 설치를 강력히 권장합니다" banner when platform skills are missing. Reports show each platform skill as ✅ installed / ❌ not installed.

### Step 1: Target & Condition Detection (before `scan`)

Follow `references/intake-questionnaire.md`:
1. Determine the target (`cloud_run` · `agent_runtime` · `gke`) from the prompt or workspace (or use `--target auto`).
2. **Auto-detect the conditions from code first** (session service usage, Memory Bank / memory tools, code executors / shell tools, `McpToolset` / `RemoteA2aAgent` / external API tools, credentials for tools called on behalf of users; `idle` only via existing Agent Substrate). Tell the user the detected result in one line each.
3. **Ask only about conditions you could not detect**, all at once, yes/no (use `ask_question` / `ask_user` if available, otherwise the plain-text template). Never ask about required items, and do not offer "full / PoC" presets — v3 has none.
4. If the user says a conditional item does not apply, exclude its ID from `--items`.

### Step 2: Scan

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <project_dir> --target <target> --items ALL
# or a subset: --items CR-F1,CR-D1,CR-S1   (legacy C* IDs also accepted)
```

Present in Korean, in this order:
1. **Gate verdict** and detected conditions.
2. Items grouped by the 5 categories (Features · Optimize · Delivery & Eval · Observability · Security): ID, title, status (`🟢`/`🟡`/`🔴`/`⚪ N/A`), and for non-green items the catalog **Check** and **Best Practice** text with reference links. Mark Preview items with 🅿️ and state that they do not block the verdict.
3. **Platform skill recommendation** for the target (table above) — always include it.
4. **Advisories** relevant to the user's situation (optional).

**`*-D1` CI/CD pipeline and `*-D2` eval gate**: `*-D1` checks that an agents-cli generated pipeline exists (`.cloudbuild/staging.yaml` or `.github/workflows/staging.yaml`); it is guidance only — the engine never generates pipeline files. If `*-D1` is RED, propose running `agents-cli scaffold enhance . --cicd-runner google_cloud_build` in the project and run it **only after explicit user approval**. `*-D2` creates the eval dataset/config and inserts the `agent-eval-gate` step (compares the `agents-cli eval run` score to `eval_threshold` and fails, because `eval run` exits 0 regardless) into the existing pipeline; without a pipeline it creates only the dataset/config and skips the gate ("D1 first"). Legacy `C7.1` maps to D1 + D2. `agents-cli infra cicd` (triggers, staging/prod CI/CD infrastructure) is a manual production-environment step: mention it as guidance, never run it.

If MCP servers or hardcoded MCP/A2A URLs are detected, load `references/agent-registry-mcp-a2a.md` and include the `AgentRegistry.get_mcp_toolset()` / `get_remote_a2a_agent()` migration recipe.

### Step 3: Preview Diffs

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" diff <project_dir> --target <target> --items <items> [--params '<json>']
# GKE with AI-generated code (GKE-S2):
# ... --target gke --params '{"gke_execution_pattern":"agent_substrate"}'   # or "agent_sandbox" for always-on agents, sidecars, Arm nodes or Autopilot
```

Summarize patched vs. new files. The engine preserves existing sizing (CPU/memory/instances/replicas), cluster mode, ingress, and agents-cli resource names, and does **not** auto-apply advisories or delegated platform BPs. Preview items are applied only if the user opts in. Ask in one line whether to apply, and **wait for explicit approval**.

### Step 4: Apply, Re-scan, Report

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" apply <project_dir> --target <target> --items <items> [--params '<json>']
uv lock   # if pyproject.toml exists
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <project_dir> --target <target> --items <items>
python3 "<SKILL_DIR>/scripts/prod_ready.py" report <project_dir> --target <target> --items <items> --format both -o <project_dir>/READINESS_REPORT.md
```

### Step 5: Deploy & Platform Skill Follow-up

Use `references/deployment-guide.md` for `terraform apply`, `uvx google-agents-cli deploy` (only after explicit approval; `--agent-identity` and `--service-account` are mutually exclusive), GKE `kubectl apply` of generated manifests, and post-deploy checks (`gcloud agent-registry agents list`, unauthenticated call returns 403 for `CR-S1`). Finish by recommending the target's platform skills from https://github.com/google/skills/tree/main/skills/cloud.

### Optional: Web Workbench

The skill has no web dependencies. For a browser UI use the repository's `web/` app (`uv run prod-ready ui <project_dir> --port 8090`), which also serves `/checklist`.
