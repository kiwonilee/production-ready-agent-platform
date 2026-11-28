---
name: prod-ready-scan
description: >-
  Slash command (/prod-ready-scan) to run a read-only Production-Readiness Checklist v4 diagnostic
  (4 balanced categories · Common + Runtime-Specific items) on the current ADK / agents-cli project.
  Never modifies files; outputs status table, 1-line explanation per item, official documentation links,
  and prompts the user to pick items for /prod-ready-fix.
---

# `/prod-ready-scan` — Read-Only Production-Readiness Diagnostic (Find)

Thin command entrypoint for the `agent-production-readiness` plugin.

## Procedure
1. Read `../agent-production-readiness/SKILL.md` (Step 0, Step 1, Step 2) and `../agent-production-readiness/references/intake-questionnaire.md`.
2. **Do NOT modify any project files** during `/prod-ready-scan`.
3. Run `doctor` (read-only) and `scan --json` using `../agent-production-readiness/scripts/prod_ready.py`:
   ```bash
   python3 <path-to-prod_ready.py> scan . --target <auto|agent_runtime|cloud_run|gke> --json
   ```
   - If the user passed conditions or a target (e.g., `/prod-ready-scan cloud_run` or `idle=true`), pass `--target` and `--conditions`.
   - Auto-detect conditions from code first; ask the user ONLY about ambiguous conditions (`idle` on GKE, undetectable `delegated`).
4. Present the diagnostic report clearly in 3 concise sections:
   - **Section A · Core & Conditional Checklist** grouped by the **4 balanced categories** (`RS · Runtime & State`, `ID · Identity & Governance`, `SF · Safety & Guardrails`, `DO · Delivery, Eval & Observability`), separating runtime-specific items (`AR-*`/`CR-*`/`GKE-*`) and common items (`COMMON-*`).
   - **Section B · Optional Production Add-ons (Tier 3 · opt-in, non-blocking)**: `COMMON-DO3` (BigQuery Agent Analytics), `COMMON-SF1` (Model Armor), `COMMON-SF2` (ADK Tool Confirmation HITL), and `AR-DO2` (Online Evaluation Monitor on Agent Runtime).
   - **Section C · Every item's 1-line plain-language summary + official documentation reference link(s)** from the catalog JSON.
5. End by inviting the user to choose which item IDs they want to apply next via **`/prod-ready-fix <ID1, ID2, ...>`** (e.g., `/prod-ready-fix CR-RS1, CR-ID1, CR-DO1`). Remind them that general infra BPs are covered by the target's `google/skills` platform skills.
