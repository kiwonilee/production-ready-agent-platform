# End-to-End Deployment Guide (`deployment-guide.md`, v2.1 — 15 Core Items)

## 1. Install the Skill from GitHub
```bash
rm -rf ~/.gemini/skills/agent-production-readiness && mkdir -p ~/.gemini/skills
curl -fsSL "https://github.com/kiwonilee/production-ready-agent-platform/archive/refs/heads/main.tar.gz" | tar -xz -C ~/.gemini/skills/ --strip-components=2 production-ready-agent-platform-main/skills/agent-production-readiness
```

## 2. Scan, Apply & Report
```bash
export SKILL_BIN="$HOME/.gemini/skills/agent-production-readiness/scripts/prod_ready.py"

python3 "$SKILL_BIN" scan . --target auto --items ALL
python3 "$SKILL_BIN" apply . --target auto --items ALL
uv lock
python3 "$SKILL_BIN" report . --target auto --items ALL --format both -o READINESS_REPORT.md
```

## 3. Deploy to GCP (`Terraform` + `agents-cli deploy`)
```bash
cd deployment/terraform
terraform init
terraform apply -var="project_id=${PROJECT_ID}" -var="region=${REGION}"
cd ../..

# Agent Runtime (with Agent Identity — mutually exclusive with --service-account):
uvx google-agents-cli deploy --project="${PROJECT_ID}" --region="${REGION}" --agent-identity --no-confirm-project

# Cloud Run (or traditional SA mode when not using Agent Identity):
uvx google-agents-cli deploy --project="${PROJECT_ID}" --region="${REGION}" --service-account="${SA_EMAIL}" --no-confirm-project

# Verify Agent Registry auto-registration (C4.2)
gcloud beta agent-registry agents list --location="${REGION}" --project="${PROJECT_ID}"
```
> **Note (Agent Runtime `C4.4`)**: `agent_gateway_config` inside `google_vertex_ai_reasoning_engine` requires a recent `google-beta` Terraform provider. If an older provider rejects that block, deploy the Agent Gateway resource via Terraform and bind it via `agents-cli deploy` or the Python SDK instead. Keep `agent_gateway_enforcement_mode = "DRY_RUN"` initially to prevent HTTP 498 outages before promoting to `"ENFORCED"`.
