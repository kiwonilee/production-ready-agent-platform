---
name: prod-ready-report
description: >-
  Slash command (/prod-ready-report) to refresh uv.lock if needed, re-scan the project, generate
  READINESS_REPORT.md and READINESS_REPORT.html, and provide exact GCP deploy/verification commands
  plus recommended Google Cloud platform skills (google/skills).
---

# `/prod-ready-report` — Final Verification, Report Export & Deploy Next Steps

Thin command entrypoint for the `agent-production-readiness` plugin.

## Procedure
1. Read `../agent-production-readiness/SKILL.md` (Step 4 — Verify, Report & Deploy) and `../agent-production-readiness/references/deployment-guide.md`.
2. If `pyproject.toml` was updated during `/prod-ready-fix`, run `uv lock` (when `uv` is available) so dependencies stay consistent.
3. Run the final report export using `../agent-production-readiness/scripts/prod_ready.py`:
   ```bash
   python3 <path-to-prod_ready.py> report . --target <auto|agent_runtime|cloud_run|gke> --out READINESS_REPORT.md --html READINESS_REPORT.html
   ```
4. Summarize concisely for the user:
   - Overall verdict & 4-category (`RS · ID · SF · DO`) pass counts (noting any remaining Preview or Optional Add-on items that do not block readiness).
   - Clickable paths to `READINESS_REPORT.md` and `READINESS_REPORT.html`.
   - Exact Terraform / `agents-cli deploy` / post-deploy verification commands for the target environment.
   - Recommended **Google Cloud platform skills** (`google/skills`) to run next for general infrastructure best practices (probes, PDB, non-root, VPC/NetworkPolicy, alerts).
