---
name: prod-ready-fix
description: >-
  Slash command (/prod-ready-fix) to interactively configure, preview unified diffs, and apply
  user-selected Production-Readiness Checklist v4 items ONE FEATURE AT A TIME. Asks item-specific
  configuration questions before generating diffs and never edits files without explicit user approval.
---

# `/prod-ready-fix` — Interactive Feature-by-Feature Configure, Diff & Apply

Thin command entrypoint for the `agent-production-readiness` plugin.

## Procedure
1. Read `../agent-production-readiness/SKILL.md` (Step 3 — Select, Configure & Feature-by-Feature Diff/Apply) and `../agent-production-readiness/references/intake-questionnaire.md` (§4 Per-Feature Configuration Questions).
2. Identify the target item ID(s) requested by the user (e.g., `/prod-ready-fix CR-RS1, CR-ID1`).
   - Both v4 IDs (`CR-RS1`, `COMMON-DO2`, `AR-SF1`, `GKE-RS3`) and legacy v3/v2 aliases (`CR-F1`, `CR-D2`, `AR-S3`, `C2.1`) are accepted.
   - If no item IDs were provided, run a quick read-only `scan` or list open items from the last scan and ask the user which IDs to apply.
3. **Process strictly ONE feature at a time (Feature-by-Feature Loop)**:
   For each selected item `ID`:
   - **3a. Brief recap**: Explain in 1–2 lines what `ID` changes, whether it is auto-patchable or guidance-only, and cite its official reference URL.
   - **3b. Configure**: Ask ONLY the item-specific configuration questions needed for `ID` (e.g., Agent Gateway direction `egress|ingress|both` · `DRY_RUN|ENFORCED` · Model Armor; Cloud Run `service_account|agent_identity`; GKE `terminationGracePeriodSeconds` or `agent_substrate|agent_sandbox`; `eval_threshold`; Model Armor confidence/SDP/URI; BigQuery dataset/table). Skip asking if the item has no tunable options or the user already specified them.
   - **3c. Diff preview (read-only)**: Run:
     ```bash
     python3 <path-to-prod_ready.py> diff . --target <target> --items <ID> --params '<json>'
     ```
     Show the exact unified diff and confirm `app/agent.py` business logic is untouched (or original lines preserved with `# [ORIGINAL ...]`).
   - **3d. Explicit approval & Apply**: Wait for the user's confirmation, then run:
     ```bash
     python3 <path-to-prod_ready.py> apply . --target <target> --items <ID> --params '<json>'
     ```
   - **3e. Verify & Next**: Confirm `ID` flipped to `GREEN` (or guidance verified), then advance to the next selected item.
4. After all selected items are finished, suggest running **`/prod-ready-report`** to update `uv.lock`, re-scan, and generate `READINESS_REPORT.md` / `.html`.
