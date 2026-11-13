---
name: agent-production-readiness
description: >-
  Audits, scores, and hardens local Google Cloud AI Agent projects (ADK prototypes or
  google-agents-cli projects) across a streamlined 7-Domain, 14-Item No-Regret Production-Readiness
  Checklist (C1.1–C7.1, v2) for Cloud Run and Agent Runtime. Preserves user compute sizing
  and ingress topology (never auto-upsizes CPU/memory or locks out public URLs with unrequested
  GCLB/WAF/Billing-Budget/deletion_protection mutations; emits non-scoring advisories instead),
  wires Agent Platform Managed Sessions & Memory Bank by default, runs 4-scenario failure
  simulations, generates unified diffs, applies in-place Terraform and ADK plugin patches
  (Direct VPC / PSC-I private egress when needed, real Model Armor API + Tool SSRF/IDOR guard,
  OpenTelemetry NO_CONTENT privacy, target-specific SRE alerts, per-turn invocation loop cap
  + max_output_tokens, and official agents-cli eval run CI/CD gates), exports
  READINESS_REPORT.md/.html for security sign-off, and launches a 3-Pane localhost workbench.
  Use when asked to check production readiness, harden an ADK or agents-cli project for
  production, generate production Terraform/guardrails, or prepare for an enterprise security
  review. Don't use for writing initial business prompt logic from scratch (use
  google-agents-cli-adk-code) or GKE cluster control-plane administration (use gke-productionize).
license: Apache-2.0
compatibility: Works with Antigravity, Gemini CLI, Claude Code, Cursor, and google-agents-cli (v1.5.0+). Requires Python 3.10+.
metadata:
  author: Google Cloud
  version: 2.0.0
  category: Agent Platform & Production Readiness
---

# Enterprise AI Agent Production-Readiness Skill (`agent-production-readiness` v2)

This skill transforms a local AI agent prototype (`adk create` / `Mode A`) or an existing `google-agents-cli` (`agents-cli`) project (`Mode B`) into a **100% production-ready enterprise architecture** across **14 core No-Regret checkpoints (`C1.1`–`C7.1`)** with strict runtime separation between **Cloud Run (`cloud_run`)** and **Agent Runtime (`agent_runtime`)**.

### Key v2 Design Principles
1. **Zero Over-Reaching Mutations**: Never blindly upsizes `cpu`/`memory`/`min_instances`, never forces GCLB/Cloud Armor WAF or locks out `*.run.app` (`default_uri_disabled`), never creates org-level Billing Account budgets (`budget.tf`), never forces `deletion_protection = true`, and never bloats log buckets with versioning. Sizing and edge ingress topology are surfaced as non-scoring **Advisories**.
2. **Managed-First Session & Memory (`D2`)**: Defaults cleanly to **Agent Platform Managed Sessions (`VertexAiSessionService`)** and **Memory Bank (`VertexAiMemoryBankService`)** with zero database ops burden. Self-hosted alternatives (Cloud SQL / Memorystore Redis / Vector DB) are noted briefly as optional paths.
3. **Strict `cloud_run` vs `agent_runtime` Separation**: On `agent_runtime`, 4 platform-managed items (`C1.1`, `C1.2`, `C2.1`, `C5.1`) are natively satisfied (`🟢 MANAGED_NATIVE`), `alerts.tf` uses `ReasoningEngine` metrics (never `cloud_run_revision`), and `service.tf` wires `psc_interface_config`.

All operations run **100% locally** inside the user's workspace using the self-contained engine bundled in `scripts/prod_ready.py` (located in the same skill directory as this `SKILL.md`).

---

## Reference Manuals & Bundled Assets (Progressive Disclosure)

Load these files from `references/` or `assets/` (relative to this `SKILL.md` directory) only when deeper detail is needed:

- **`references/intake-questionnaire.md`** — **READ FIRST before running `scan` or `simulate`**: Complete 14-item (`C1.1`–`C7.1`) pre-scan confirmation questions, Skip/N/A rules, 4-scenario simulation applicability, Quick Presets, and `--params` JSON keys.
- **`references/checklist-rubric.md`** — Full 7-Domain (`D1`–`D7`), 14-Item (`C1.1`–`C7.1`) scoring formula, severity definitions, and `Cloud Run` vs. `Agent Runtime` comparison matrix.
- **`references/cloud-run-hardening.md`** — `agents-cli` v1.5.0 Cloud Run resource compatibility (`google_cloud_run_v2_service.app`), Managed Sessions & Memory Bank wiring, Direct VPC Egress, and SRE alerts.
- **`references/agent-runtime-hardening.md`** — `google_vertex_ai_reasoning_engine.app` hardening, 4 `MANAGED_NATIVE` platform items vs. 10 required patches, PSC Interface (`google_compute_network_attachment`) wiring, and `ReasoningEngine` monitoring metrics.
- **`references/deployment-guide.md`** — Step-by-step guide for importing this skill into Antigravity and deploying the hardened agent to GCP via Terraform and `agents-cli deploy`.
- **`assets/terraform/`** — Reference drop-in HCL module (`agent_runtime_production_ready.tf`).
- **`assets/python/`** — Reference ADK plugins (`services.py`, `model_armor_plugin.py`, `cost_guard_plugin.py`).

---

## Step-by-Step Agent Workflow

Let `<SKILL_DIR>` be the directory containing this `SKILL.md` file. Always execute `<SKILL_DIR>/scripts/prod_ready.py` with `python3` (for `scan`, `simulate`, `diff`, `apply`, `report` — requires **zero external packages**) or `uv run` (for `ui`).

### Step 1: Pre-Scan Workload Selection (MANDATORY BEFORE `scan` / `simulate`)

> **HARD RULES**:
> 1. Do **NOT** run `prod_ready.py scan` or `prod_ready.py simulate` before confirming the user's workload profile — **unless** the user already specified a preset/profile or explicitly asked for `"전체 항목(ALL) / 기본값"`.
> 2. **KEEP STEP 1 ULTRA-SHORT (~10 lines!)**: Do **NOT** dump raw checklist IDs (`C1.1`–`C7.1`), long tables, or low-level numeric knobs in Step 1. Defer optional parameter tuning to Step 3 (right before `apply`).

Silently inspect the target runtime (`cloud_run` vs `agent_runtime`) from the prompt/workspace first, then ask using **whichever mode fits your CLI**:

#### Mode A — Interactive Multiple-Choice Tool (`ask_question` / `ask_user`, Preferred in `agy`)
1. First ask **1 single question** with these **4 clean options**:
   - `풀 프로덕션 (사내 DB/VPC 연동 포함) — 전체 14개 핵심 하드닝 (권장)`
   - `표준 프로덕션 (외부/Public API 기반) — 사내 VPC 제외 13개 하드닝`
   - `경량 / PoC — 단발성 · 핵심 기본선 8개 최소 비용`
   - `직접 맞춤 조합하기 (3가지 환경 질문으로 선택)`
2. If the user selects one of the first 3 presets, **proceed immediately to Step 2**.
3. **Only if** the user selects **`직접 맞춤 조합하기`**, ask these **3 short multiple-choice questions together (`Question 1/3`–`3/3`)**:
   - **Q1. 대화 기억**: `단발성 질의응답 (Stateless)` vs `멀티턴 대화 & 장기기억 (Managed Sessions & Memory Bank)`
   - **Q2. 사내 프라이빗 망 연동**: `Gemini / 공개 API만 호출` vs `사내 프라이빗 DB · 내부 VPC 연동`
   - **Q3. 운영 단계 & PII 가드레일**: `PoC / 개발 테스트` vs `실제 상용 운영 · PII 보호 & SRE 알람`

#### Mode B — Plain-Text Fallback (for CLIs without an interactive selection tool)
Print this exact compact template verbatim:

```text
진단 전 운영 환경만 간단히 선택해 주세요. 번호(1~3) 하나만 입력하시면 바로 진행합니다.

  [1] 풀 프로덕션 (사내 VPC 포함) — 멀티턴 · 사내 DB/VPC · 가드레일 · 전체 14개 (권장)
  [2] 표준 프로덕션 (외부 API만)  — 멀티턴 · 가드레일 · 사내 VPC 제외 13개
  [3] 경량 / PoC                 — 단발성 · 최소 비용 · 핵심 기본선 8개

※ 직접 조합하려면 3개만 A/B로 답해주세요 (예: B, A, B):
  1. 대화 기억:  A) 단발성 (Stateless)  / B) 멀티턴·장기기억 (Managed)
  2. 사내 연동:  A) 외부 공개 API만     / B) 사내 프라이빗 DB·VPC 연동
  3. 운영·민감:  A) PoC·개발 테스트     / B) 실운영·PII 가드레일·SRE 알람
```

#### Deterministic Answer → `--items` Mapping (Internal — see `references/intake-questionnaire.md`)
- **풀 프로덕션 (사내 VPC 포함)** (`B, B, B`): `--items ALL` (`14 items`: `C1.1,C1.2,C2.1,C2.2,C3.1,C4.1,C4.2,C4.3,C5.1,C5.2,C5.3,C6.1,C6.2,C7.1`)
- **표준 프로덕션 (외부 API만)** (`B, A, B`): `--items C1.1,C1.2,C2.1,C2.2,C4.1,C4.2,C4.3,C5.1,C5.2,C5.3,C6.1,C6.2,C7.1` (`13 items` — skips `C3.1` private VPC egress)
- **경량 / PoC** (`A, A, A`): `--items C1.1,C1.2,C4.1,C4.3,C5.1,C5.2,C6.1,C6.2` (`8 items`)
- **Custom 3-Answer Union**: Start from baseline `C1.1,C1.2,C4.1,C4.3,C5.1,C5.2,C6.1,C6.2`, then union:
  - `Q1 = 멀티턴·장기기억` (`1=B`) → `+ C2.1,C2.2` (Managed Sessions & Memory Bank)
  - `Q2 = 사내 프라이빗 DB·VPC 연동` (`2=B`) → `+ C3.1` (Direct VPC Egress / PSC-I)
  - `Q3 = 실운영·PII 가드레일·SRE 알람` (`3=B`) → `+ C4.2,C5.3,C7.1` (Model Armor + SRE Alerts + Eval Gate)

Proceed immediately to Step 2 once the selection is confirmed.

### Step 2: Scan the Target Project & Run Tailored 4-Scenario Failure Simulation

Execute `scan` and `simulate` using the user-confirmed `--target` and `--items` scope so N/A items are cleanly skipped:

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <target_project_dir> --target <target> --items <confirmed_items>
python3 "<SKILL_DIR>/scripts/prod_ready.py" simulate <target_project_dir> --target <target> --items <confirmed_items>
```

- Present the **Weighted Readiness Score (`0`–`100.0`)**, the **Gate Verdict**, `RED` / `AMBER` blockers, **Non-Scoring Advisories** (compute sizing load-test guidance + ingress/auth architecture note + Managed Session note), **Skipped (N/A) items**, and the **Applicable Simulation Result** (`passed / applicable`) in a Markdown table.
- Note clearly how `cloud_run` vs `agent_runtime` differ: on `agent_runtime`, 4 items (`C1.1`, `C1.2`, `C2.1`, `C5.1`) are automatically credited as `🟢 MANAGED_NATIVE`.

### Step 3: Preview Tailored Unified Diffs & Optional Parameter Overrides

Before modifying any project files, preview the exact unified diffs using the confirmed `--items` and optional `--params`:

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" diff <target_project_dir> --target <target> --items <confirmed_items>
```

Summarize the key changes for the user (existing files patched in-place vs. new drop-in files created, preserving 100% of `agents-cli` resource names, ingress accessibility, and user CPU/memory/min_instances sizing), and ask in **one brief line** whether to apply.

### Step 4: Apply Tailored Production Hardening Patches & Verify

Once confirmed, execute `apply` with the exact same `--items`:

```bash
python3 "<SKILL_DIR>/scripts/prod_ready.py" apply <target_project_dir> --target <target> --items <confirmed_items>
```

Then refresh `uv.lock` (if `pyproject.toml` exists), re-run tailored `scan` and `simulate` to confirm `100.0 / 100.0` (`🟢 Production Ready`) and `all_passed` on the applicable scenarios, and generate the Enterprise Security Review reports (`READINESS_REPORT.md` and `READINESS_REPORT.html`):

```bash
uv lock
python3 "<SKILL_DIR>/scripts/prod_ready.py" scan <target_project_dir> --target <target> --items <confirmed_items>
python3 "<SKILL_DIR>/scripts/prod_ready.py" simulate <target_project_dir> --target <target> --items <confirmed_items>
python3 "<SKILL_DIR>/scripts/prod_ready.py" report <target_project_dir> --target <target> --items <confirmed_items> --format both -o <target_project_dir>/READINESS_REPORT.md
```

### Step 5: Launch the Interactive 3-Pane Web Workbench (Optional)

When the user wants to visually explore the 14 items, customize infrastructure parameters with live diff regeneration, or download a `.zip` bundle in their browser:

```bash
uv run "<SKILL_DIR>/scripts/prod_ready.py" ui <target_project_dir> --port 8090
```

### Step 6: Guide Cloud Deployment (`Terraform` + `agents-cli deploy`)

After hardening verification completes, consult `references/deployment-guide.md` to guide the user through:
1. Checking/installing `terraform` (if missing in Cloud Shell) and running `terraform init && terraform plan && terraform apply` in `deployment/terraform/`.
2. Running `uvx google-agents-cli deploy` (only after explicit human approval; remind the user to size CPU/memory/min_instances based on load testing).
3. Running post-deployment verification (`uvx google-agents-cli eval run --dataset ... --metrics ...`).
