# Production Ready Agent Platform — Always-On Guardrails

These lightweight invariants apply when working on Google Cloud AI Agent projects (`google-adk` / `google-agents-cli`) targeting **Agent Runtime**, **Cloud Run**, or **GKE**. Detailed checklist logic and scripts live in the `agent-production-readiness` skill (loaded on demand to keep context lean).

## Non-Negotiable Production Guardrails
1. **Never overwrite agent logic silently**: Preserve `app/agent.py` prompts, tools, and business logic. When wiring ADK plugins or memory hooks, keep original lines commented (`# [ORIGINAL ...]`) and require user approval on a unified diff first.
2. **Never bulk-apply unrequested checklist changes**: Always run a read-only diagnostic first (`scan`), explain each item in 1 line with official references, let the user select which item IDs to fix, ask item-specific configuration questions, and apply **one feature at a time** after diff approval.
3. **Zero insecure defaults**: Flag `allUsers` / `--allow-unauthenticated` on Cloud Run (`CR-ID1`), `InMemorySessionService` in production (`*-RS1`), prompt/response body logging in traces (`*-DO1`), and static PATs/API keys in MCP tool headers (`COMMON-ID1`).
4. **Safe rollout for Agent Gateway**: Always default `Agent Gateway` (`AR-SF1`, `CR-SF1`, `GKE-SF2`) enforcement to `DRY_RUN` first and switch to `ENFORCED` only after log verification.
5. **Pair with Google Cloud Platform Skills**: General container/infra BPs (probes, PDB, non-root, VPC/NetworkPolicy, alerts) are delegated to `google/skills` — always remind the user to run the target's recommended platform skills alongside this checklist.

## Slash Commands (Auto-Registered by This Plugin)
| Command | Purpose |
| :--- | :--- |
| `/prod-ready-doctor` | Preflight `agents-cli` and Google Cloud platform skills (`google/skills`) |
| `/prod-ready-scan` | Read-only v4 checklist scan (4 categories · 31 unique items) + 1-line item descriptions & references |
| `/prod-ready-fix` | Configure, preview diff, and apply selected item IDs **one feature at a time** |
| `/prod-ready-report` | Refresh `uv.lock`, re-scan, generate `READINESS_REPORT.md/.html`, and summarize deploy & platform skills |
