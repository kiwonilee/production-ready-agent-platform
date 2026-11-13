# End-to-End Deployment Guide (`deployment-guide.md`, v2 — 14 Core Items)

## 1. Install the Skill from GitHub
```bash
rm -rf ~/.gemini/skills/agent-production-readiness && mkdir -p ~/.gemini/skills
curl -fsSL "https://github.com/kiwonilee/production-ready-agent-platform/archive/refs/heads/main.tar.gz" | tar -xz -C ~/.gemini/skills/ --strip-components=2 production-ready-agent-platform-main/skills/agent-production-readiness
```

## 2. Scan, Simulate, Apply & Report
```bash
export SKILL_BIN="$HOME/.gemini/skills/agent-production-readiness/scripts/prod_ready.py"

python3 "$SKILL_BIN" scan . --target auto --items ALL
python3 "$SKILL_BIN" simulate . --target auto --items ALL
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

uvx google-agents-cli deploy --project="${PROJECT_ID}" --region="${REGION}" --service-account="${SA_EMAIL}" --no-confirm-project
```
