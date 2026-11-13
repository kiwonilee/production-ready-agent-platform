---
name: agent-production-readiness
description: >-
  Audits and hardens local Google Cloud AI Agent projects (ADK prototypes or
  google-agents-cli projects) across a 7-Domain, 15-Item "True No-Regret" + GEAP Governance
  Production-Readiness Checklist (C1.1–C7.1, v2.1) for Cloud Run and Agent Runtime.
  Applies Agent Identity (SPIFFE ID) & Agent Registry auto-registration (`C4.2`) to BOTH targets,
  and applies Agent Gateway (`C4.4`, `AGENT_TO_ANYWHERE` + UAP V2 `DRY_RUN` default to prevent HTTP 498)
  strictly to Agent Runtime while adding a non-blocking staged-adoption architecture comment to Cloud Run.
  Preserves user compute sizing, Fail-Fast retry defaults (never forces attempts=5), Thinking-model
  token headroom (max_output_tokens=0 opt-in), and ingress topology (never locks out public
  URLs or injects unsolicited GCLB/WAF/Model Armor/Billing-Budget/deletion_protection mutations;
  emits architecture & governance advisories for Model Armor Floor Settings and Provisioned Throughput SLA instead).
  Wires Agent Platform Managed Sessions & Memory Bank by default,
  generates unified diffs, applies in-place Terraform and ADK plugin patches, and exports
  READINESS_REPORT.md/.html (including a Post-Setup Decision Guide) for security sign-off. Use when asked to check production readiness, harden
  an ADK or agents-cli project for production, generate production Terraform/finops guards, or
  prepare for an enterprise security review. Don't use for writing initial business prompt
  logic from scratch (use google-agents-cli-adk-code) or GKE cluster control-plane
  administration (use gke-productionize).
license: Apache-2.0
compatibility: Works with Antigravity, Gemini CLI, Claude Code, Cursor, and google-agents-cli (v1.5.0+). Requires Python 3.10+.
metadata:
  author: Google Cloud
  version: 2.1.0
  category: Agent Platform & Production Readiness
---

# Enterprise AI Agent Production-Readiness Skill (`agent-production-readiness` v2.1)

This skill transforms a local AI agent prototype (`adk create` / `Mode A`) or an existing `google-agents-cli` (`agents-cli`) project (`Mode B`) into a **100% production-ready enterprise architecture** across **15 core "True No-Regret" + GEAP Governance checkpoints (`C1.1`–`C7.1`)** with strict runtime separation between **Cloud Run (`cloud_run`)** and **Agent Runtime (`agent_runtime`)**.

### Key v2.1 Design Principles
1. **Zero Over-Reaching Mutations**: Never blindly upsizes `cpu`/`memory`/`min_instances`, never forces GCLB/Cloud Armor WAF or locks out `*.run.app` (`default_uri_disabled`), never injects unrequested Model Armor/tool SSRF plugins into individual agents, never creates org-level Billing Account budgets (`budget.tf`), never forces `deletion_protection = true`, and never bloats log buckets with versioning. Sizing, ingress auth, Model Armor Floor Settings, and production SLA (**Provisioned Throughput GSU / Priority PayGo**) are surfaced as architecture & governance **Advisories**.
2. **GEAP Governance Split (`C4.2` & `C4.4`)**:
   - **`C4.2` Agent Identity (SPIFFE ID) & Agent Registry**: Active (`TIER_1_AUTO`) on **BOTH** `cloud_run` (`functional_type = "FUNCTIONAL_TYPE_AGENT"`, `identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"`) and `agent_runtime` (`spec.identity_type = "AGENT_IDENTITY"`), enabling `agentregistry.googleapis.com` & `apphub.googleapis.com` and cleanly resolving mutual exclusivity with `service_account`. **Context-Aware MCP/A2A Support**: Automatically detects Cloud Run **MCP Servers** (`FastMCP` / `mcp.server` → injects `FUNCTIONAL_TYPE_MCP_SERVER` while preserving dedicated least-privilege SA) and flags hardcoded remote MCP/A2A URLs (`MCP_SERVER_URL`, `StreamableHTTPConnectionParams`, `RemoteA2aAgent`) as `AMBER` with an `AgentRegistry` Lazy Discovery refactoring guide — while producing **zero noise** on standalone single-agent projects.
   - **`C4.4` Agent Gateway**: Active (`TIER_1_AUTO`) **ONLY on `agent_runtime`** (`google_network_services_agent_gateway` + UAP V2 `AuthzExtension`/`AuthzPolicy` defaulting to `iamEnforcementMode = "DRY_RUN"` to prevent HTTP 498 outages + `agent_gateway_config` + `Dockerfile` `AGENT_GATEWAY_ROOT_CERTIFICATES`). On **`cloud_run`**, injects **only** a non-blocking staged-adoption comment (`TIER_2_TEMPLATE`) into `service.tf` *(Cloud Run은 Direct VPC Egress + SWP/VPC-SC + IAP + Model Armor라는 검증된 GA 인프라 보안 위에 Agent Identity / Registry / Self-Managed AGW를 단계적으로 얹는 접근이 가장 현실적입니다)* without generating Gateway infrastructure.
3. **Thinking-Model & Retry-Storm Safe Defaults**: `CostTokenGuardrailPlugin` caps per-turn (`invocation_id`) runaway loops while defaulting `max_output_tokens=0` (opt-in) so Gemini 2.5/3.x Thinking models aren't truncated. `C6.2` preserves `agents-cli`'s Fail-Fast `HttpRetryOptions(attempts=3)` + `GOOGLE_CLOUD_LOCATION=global` instead of forcing `attempts=5`.
4. **Managed-First Session & Memory (`D2`)**: Defaults cleanly to **Agent Platform Managed Sessions (`VertexAiSessionService`)** and **Memory Bank (`VertexAiMemoryBankService`)** with zero database ops burden. Self-hosted alternatives (Cloud SQL / Memorystore Redis / Vector DB) are noted briefly as optional paths.
5. **Strict `cloud_run` vs `agent_runtime` Separation**: On `agent_runtime`, 4 platform-managed items (`C1.1`, `C1.2`, `C2.1`, `C5.1`) are natively satisfied (`🟢 MANAGED_NATIVE`), `alerts.tf` uses `ReasoningEngine` metrics (never `cloud_run_revision`), and `service.tf` wires `psc_interface_config` and `agent_gateway_config`.

All operations run **100% locally** inside the user's workspace using the self-contained engine bundled in `scripts/prod_ready.py` (located in the same skill directory as this `SKILL.md`).

---

## Reference Manuals & Bundled Assets (Progressive Disclosure)

Load these files from `references/` or `assets/` (relative to this `SKILL.md` directory) only when deeper detail is needed:

- **`references/intake-questionnaire.md`** — **READ FIRST before running `scan`**: Complete 15-item (`C1.1`–`C7.1`) pre-scan confirmation questions, Skip/N/A rules, Quick Presets, and `--params` JSON keys.
- **`references/checklist-rubric.md`** — Full 7-Domain (`D1`–`D7`), 15-Item (`C1.1`–`C7.1`) compliance gate criteria, severity definitions, and `Cloud Run` vs. `Agent Runtime` comparison matrix.
- **`references/cloud-run-hardening.md`** — `agents-cli` v1.5.0 Cloud Run resource compatibility (`google_cloud_run_v2_service.app`), Agent Identity & Agent Registry (`C4.2`), staged AGW advisory (`C4.4`), Managed Sessions & Memory Bank wiring, Direct VPC Egress, and SRE alerts.
- **`references/agent-runtime-hardening.md`** — `google_vertex_ai_reasoning_engine.app` hardening, 4 `MANAGED_NATIVE` platform items vs. 11 active patches, Agent Identity (`C4.2`), Agent Gateway UAP V2 `DRY_RUN` (`C4.4`), PSC Interface (`google_compute_network_attachment`) wiring, and `ReasoningEngine` monitoring metrics.
- **`references/agent-registry-mcp-a2a.md`** — **Conditional (Load ONLY when MCP Server or hardcoded MCP/A2A URLs are detected or requested)**: Cloud Run MCP Server (`FUNCTIONAL_TYPE_MCP_SERVER`), external MCP `toolspec.json` (`google_agent_registry_service`), A2A `AgentCard`, ADK `AgentRegistry` Lazy Discovery (`get_mcp_toolset()`, `get_remote_a2a_agent()`), and Agent Gateway MCP Tool-Level CEL (`iap.googleapis.com/mcp.toolName`, `mcp.tool.isReadOnly`).
- **`references/deployment-guide.md`** — Step-by-step guide for importing this skill into Antigravity and deploying the hardened agent to GCP via Terraform and `agents-cli deploy`.
- **`assets/terraform/`** — Reference drop-in HCL module (`agent_runtime_production_ready.tf`).
- **`assets/python/`** — Reference ADK plugins (`services.py`, `cost_guard_plugin.py`).

---

## Production-Readiness 4-Stage Flow

```text
1. Scanning (정적 진단) ──▶ 2. Hardening (조치 · 재검사) ──▶ 3. Deploy (배포) ──▶ 4. Runtime Audit (라이브 점검)
```

## Step-by-Step Agent Workflow

Let `<SKILL_DIR>` be the directory containing this `SKILL.md` file. Always execute `<SKILL_DIR>/scripts/prod_ready.py` with `python3` (requires **zero external packages** — pure Python stdlib).

### Step 1: Pre-Scan Workload Selection (Scanning Sub-Work · MANDATORY BEFORE `scan`)

> **HARD RULES**:
> 1. Do **NOT** run `prod_ready.py scan` before confirming the user's workload profile — **unless** the user already specified a preset/profile or explicitly asked for `"전체 항목(ALL) / 기본값"`.
> 2. **KEEP STEP 1 ULTRA-SHORT (~10 lines!)**: Do **NOT** dump raw checklist IDs (`C1.1`–`C7.1`), long tables, or low-level numeric knobs in Step 1. Defer optional parameter tuning to Step 3 (right before `apply`).

Silently inspect the target runtime (`cloud_run` vs `agent_runtime`) from the prompt/workspace first, then ask using **whichever mode fits your CLI**:

#### Mode A — Interactive Multiple-Choice Tool (`ask_question` / `ask_user`, Preferred in Antigravity CLI)
1. First ask **1 single question** with these **4 clean options**:
   - `풀 프로덕션 (사내 DB/VPC 연동 포함) — 전체 15개 핵심 하드닝 (권장)`
   - `표준 프로덕션 (외부/Public API 기반) — 사내 VPC 제외 14개 하드닝`
   - `경량 / PoC — 단발성 · 핵심 기본선 9개 최소 비용`
   - `직접 맞춤 조합하기 (3가지 환경 질문으로 선택)`
2. If the user selects one of the first 3 presets, **proceed immediately to Step 2**.
3. **Only if** the user selects **`직접 맞춤 조합하기`**, ask these **3 short multiple-choice questions together (`Question 1/3`–`3/3`)**:
   - **Q1. 대화 기억**: `단발성 질의응답 (Stateless)` vs `멀티턴 대화 & 장기기억 (Managed Sessions & Memory Bank)`
   - **Q2. 사내 프라이빗 망 연동**: `Gemini / 공개 API만 호출` vs `사내 프라이빗 DB · 내부 VPC 연동`
   - **Q3. 운영 거버넌스 & 배포 게이트**: `PoC / 개발 테스트` vs `실제 상용 운영 · Agent Gateway(AR) · SRE 알람 · CI/CD Eval 게이트`

#### Mode B — Plain-Text Fallback (for CLIs without an interactive selection tool)
Print this exact compact template verbatim:

```text
진단 전 운영 환경만 간단히 선택해 주세요. 번호(1~3) 하나만 입력하시면 바로 진행합니다.

  [1] 풀 프로덕션 (사내 VPC 포함) — 멀티턴 · 사내 DB/VPC · Identity/AGW · SRE 알람/Eval · 전체 15개 (권장)
  [2] 표준 프로덕션 (외부 API만)  — 멀티턴 · Identity/AGW · SRE 알람/Eval · 사내 VPC 제외 14개
  [3] 경량 / PoC                 — 단발성 · 최소 비용 · Identity 포함 핵심 기본선 9개

※ 직접 조합하려면 3개만 A/B로 답해주세요 (예: B, A, B):
  1. 대화 기억:  A) 단발성 (Stateless)  / B) 멀티턴·장기기억 (Managed)
  2. 사내 연동:  A) 외부 공개 API만     / B) 사내 프라이빗 DB·VPC 연동
  3. 운영·검증:  A) PoC·개발 테스트     / B) 실운영·AGW(AR)·SRE 알람·CI/CD Eval 게이트
```

#### Deterministic Answer → `--items` Mapping (Internal — see `references/intake-questionnaire.md`)
- **풀 프로덕션 (사내 VPC 포함)** (`B, B, B`): `--items ALL` (`15 items`: `C1.1,C1.2,C2.1,C2.2,C3.1,C4.1,C4.2,C4.3,C4.4,C5.1,C5.2,C5.3,C6.1,C6.2,C7.1`)
- **표준 프로덕션 (외부 API만)** (`B, A, B`): `--items C1.1,C1.2,C2.1,C2.2,C4.1,C4.2,C4.3,C4.4,C5.1,C5.2,C5.3,C6.1,C6.2,C7.1` (`14 items` — skips `C3.1` private VPC egress)
- **경량 / PoC** (`A, A, A`): `--items C1.1,C1.2,C4.1,C4.2,C4.3,C5.1,C5.2,C6.1,C6.2` (`9 items`)
- **Custom 3-Answer Union**: Start from baseline `C1.1,C1.2,C4.1,C4.2,C4.3,C5.1,C5.2,C6.1,C6.2`, then union:
  - `Q1 = 멀티턴·장기기억` (`1=B`) → `+ C2.1,C2.2` (Managed Sessions & Memory Bank)
  - `Q2 = 사내 프라이빗 DB·VPC 연동` (`2=B`) → `+ C3.1` (Direct VPC Egress / PSC-I)
  - `Q3 = 실운영·AGW(AR)·SRE 알람·CI/CD Eval` (`3=B`) → `+ C4.4,C5.3,C7.1` (Agent Gateway / Cloud Run Advisory + SRE Alerts + Eval Gate)

Proceed immediately to Step 2 once the selection is confirmed.

### Step 2: Scan the Target Project

Execute `scan` using the user-confirmed `--target` and `--items` scope so N/A items are cleanly skipped:

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <target_project_dir> --target <target> --items <confirmed_items>
```

- Present the **Gate Verdict**, **Item Compliance Summary (`🟢 Passed / Total`, `🟡 Amber`, `🔴 Red` counts)**, `RED` / `AMBER` blockers, **Architecture & Governance Advisories** (compute sizing load-test guidance + ingress/auth topology + Managed Session note + **AI Security Governance / Model Armor Floor Settings** + **Production SLA via Provisioned Throughput GSU / Priority PayGo**), and **Skipped (N/A) items** in a Markdown table.
- Note clearly how `cloud_run` vs `agent_runtime` differ: on `agent_runtime`, 4 items (`C1.1`, `C1.2`, `C2.1`, `C5.1`) are automatically credited as `🟢 MANAGED_NATIVE`, and `C4.4` deploys Agent Gateway (`DRY_RUN` default); on `cloud_run`, `C4.4` adds only a staged-adoption architecture comment in `service.tf`.
- **Conditional MCP / A2A Follow-Up**: If `scan` detects a Cloud Run MCP Server (`FUNCTIONAL_TYPE_MCP_SERVER`) or flags hardcoded MCP/A2A URLs in `C4.2` (`AMBER`), load `references/agent-registry-mcp-a2a.md` and present the `AgentRegistry.get_mcp_toolset()` / `get_remote_a2a_agent()` migration diff alongside the Terraform patches. Otherwise skip it completely.

### Step 3: Preview Tailored Unified Diffs & Optional Parameter Overrides

Before modifying any project files, preview the exact unified diffs using the confirmed `--items` and optional `--params`:

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" diff <target_project_dir> --target <target> --items <confirmed_items>
```

Summarize the key changes for the user (existing files patched in-place vs. new drop-in files created, preserving 100% of `agents-cli` resource names, Fail-Fast retry defaults, ingress accessibility, and user CPU/memory/min_instances sizing), and ask in **one brief line** whether to apply.

### Step 4: Apply Tailored Production Hardening Patches & Verify

Once confirmed, execute `apply` with the exact same `--items`:

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" apply <target_project_dir> --target <target> --items <confirmed_items>
```

Then refresh `uv.lock` (if `pyproject.toml` exists), re-run tailored `scan` to confirm all active items pass (`🟢 Production Ready`, 0 RED / 0 AMBER), and generate the Enterprise Security Review reports (`READINESS_REPORT.md` and `READINESS_REPORT.html`):

```bash
uv lock
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <target_project_dir> --target <target> --items <confirmed_items>
python3 "<SKILL_DIR>/scripts/prod_ready.py" report <target_project_dir> --target <target> --items <confirmed_items> --format both -o <target_project_dir>/READINESS_REPORT.md
```

### Step 5: Optional Web Workbench (Hosted or Repository `web/` App)

The standalone skill stays lightweight (zero web assets or FastAPI dependencies). If the user wants a browser-based JSON Runtime Wizard or visual diff workbench, they can use the separate `web/` app from the repository (`uv run prod-ready ui <target_project_dir> --port 8090`).

### Step 6: Guide Cloud Deployment (`Terraform` + `agents-cli deploy`)

After hardening verification completes, consult `references/deployment-guide.md` to guide the user through:
1. Checking/installing `terraform` (if missing in Cloud Shell) and running `terraform init && terraform plan && terraform apply` in `deployment/terraform/`. *(Note: on Agent Runtime, `agent_gateway_config` inside `google_vertex_ai_reasoning_engine` requires a recent `google-beta` provider version; if an older provider rejects that block, bind the Agent Gateway via `agents-cli deploy` or the Python SDK instead.)*
2. Running `uvx google-agents-cli deploy` (only after explicit human approval; remind the user that `--agent-identity` and `--service-account` are mutually exclusive, and to size CPU/memory/min_instances based on load testing).
3. Running post-deployment verification (`gcloud beta agent-registry agents list` and `uvx google-agents-cli eval run --dataset ... --metrics ...`).
