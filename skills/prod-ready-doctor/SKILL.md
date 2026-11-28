---
name: prod-ready-doctor
description: >-
  Slash command (/prod-ready-doctor) to run the Production Ready Agent Platform dependency preflight.
  Checks agents-cli >= 1.5.0, official google-agents-cli-* skills, and recommended Google Cloud platform
  skills (google/skills) for Agent Runtime, Cloud Run, or GKE, and optionally installs missing platform skills
  with user confirmation.
---

# `/prod-ready-doctor` — Preflight & Platform Skill Check

Thin command entrypoint for the `agent-production-readiness` plugin.

## Procedure
1. Locate the core skill engine at `../agent-production-readiness/scripts/prod_ready.py` (relative to this skill directory; fall back to `skills/agent-production-readiness/scripts/prod_ready.py` or `~/.gemini/skills/agent-production-readiness/scripts/prod_ready.py`).
2. Detect or use the target specified by the user (`agent_runtime`, `cloud_run`, `gke`, or `auto`).
3. Run read-only preflight first:
   ```bash
   python3 <path-to-prod_ready.py> doctor . --target <target>
   ```
4. Present the dependency table cleanly:
   - Required tools: `agents-cli >= 1.5.0` and `google-agents-cli-*` skills (`agents-cli setup --skip-auth`).
   - Strongly recommended Google Cloud platform skills (`google/skills`) for the detected target.
5. If recommended platform skills are missing, **ask the user for consent** before running `--install-platform-skills`. Never install skills without explicit confirmation.
6. Suggest `/prod-ready-scan` as the next step once preflight passes.
