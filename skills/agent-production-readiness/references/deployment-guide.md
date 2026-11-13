# End-to-End Deployment Guide (Skill Import & GCP Production Rollout, v2)

## Part 0: Prerequisites & CLI Installation

| Requirement | Tool | Purpose | Installation Command |
| :--- | :--- | :--- | :--- |
| **Required** | **`Python 3.10+` & `uv`** | Running `prod-ready` (`scan`, `diff`, `apply`, `report`, `ui`) and `google-agents-cli` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Recommended** | **`google-agents-cli` (`agents-cli` v1.5.0+)** | Scaffolding ADK agents (`agents-cli scaffold`), running evals (`eval run`), and deploying to GCP (`deploy`) | `uv tool install "google-agents-cli~=1.5.0"`<br/>`uvx google-agents-cli setup`<br/>`agents-cli info` |
| **Optional (Chat Mode)** | **Antigravity IDE / CLI** (or Gemini CLI, Claude Code) | Conversational skill invocation in chat | Download from `https://antigravity.google/` |
| **Optional (Cloud Deploy)** | **`gcloud` CLI & `Terraform >= 1.5`** | Provisioning GCP infrastructure and deploying containers | `gcloud auth login && gcloud auth application-default login` |

---

## Part 1: Importing `agent-production-readiness` into AI Coding Agents

This skill conforms to the **[Agent Skills Open Standard (`agentskills.io`)](https://agentskills.io/home)** and works out-of-the-box in **Antigravity**, **Gemini CLI**, **Claude Code**, **Cursor**, and **`google-agents-cli`**.

### Option A: Import into Antigravity (Standalone Skill)
```bash
# 1. Shared Antigravity skill installation (available in all workspaces)
mkdir -p ~/.gemini/skills
cp -r skills/agent-production-readiness ~/.gemini/skills/agent-production-readiness

# OR 2. Workspace-local installation (.agents/skills/)
mkdir -p .agents/skills
cp -r skills/agent-production-readiness .agents/skills/agent-production-readiness
```

Because `skills/agent-production-readiness/scripts/prod_ready.py` bundles the complete scanning, patching, reporting, and localhost UI engine, **it operates 100% standalone inside any project workspace**.

### Option B: Import as an Antigravity Plugin Bundle
```bash
mkdir -p ~/.gemini/config/plugins
cp -r . ~/.gemini/config/plugins/agent-production-readiness
```

### Option C: Install via `npx skills` or `uv tool`
```bash
npx skills add <repo-path-or-url>
# Or install the global `prod-ready` CLI binary via uv
uv tool install .
```

---

## Part 2: Deploying a Hardened Agent to Google Cloud (`Cloud Run` or `Agent Runtime`)

### Step 1: Audit & Harden Your Local Agent Project
Run the scanner, failure simulator, and apply either all 17 items (`--items ALL`) or a workload-tailored subset (`--items ...` with optional `--params` JSON):

```bash
export SKILL_BIN="$HOME/.gemini/skills/agent-production-readiness/scripts/prod_ready.py"

# 1. Scan & simulate current project (pass --items for tailored workloads, e.g. PoC/Internal)
python3 "$SKILL_BIN" scan . --target auto --items ALL
python3 "$SKILL_BIN" simulate . --target auto --items ALL

# 2. Preview unified diffs (e.g., ingress_mode="internal_iap" for internal Cloud Run apps)
python3 "$SKILL_BIN" diff . --target auto --items ALL --params '{"ingress_mode":"external_waf"}'

# 3. Apply hardening patches in-place (reaches 100.0 / 100.0 for selected scope)
python3 "$SKILL_BIN" apply . --target auto --items ALL --params '{"ingress_mode":"external_waf"}'

# 4. Generate READINESS_REPORT.md & READINESS_REPORT.html for security sign-off
python3 "$SKILL_BIN" report . --target auto --items ALL --format both -o READINESS_REPORT.md
```

### Step 2: Provision Hardened GCP Infrastructure via Terraform
```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-east1"

cd deployment/terraform
terraform init
terraform plan \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="vpc_network_name=default" \
  -var="monthly_budget_amount_usd=1000"

# Apply after reviewing the plan
terraform apply \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"
cd ../..
```

### Step 3: Deploy the Agent Application via `agents-cli deploy`

#### Target A: Deploying to **Cloud Run** (`cloud_run`)
> **Sizing Tip**: Start with your default CPU/memory/min_instances, run `tests/load_test/load_test.py` (Locust) against staging, and pass `--cpu`, `--memory`, `--concurrency`, `--min-instances` tuned to your real latency/throughput SLA.

```bash
SA_EMAIL="$(gcloud iam service-accounts list --project=${PROJECT_ID} --filter='email ~ -app@' --format='value(email)' | head -n 1)"

uvx google-agents-cli deploy \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --service-account="${SA_EMAIL}" \
  --iap
```

#### Target B: Deploying to **Agent Runtime** (`agent_runtime`)
```bash
SA_EMAIL="$(gcloud iam service-accounts list --project=${PROJECT_ID} --filter='email ~ -app@' --format='value(email)' | head -n 1)"
PSC_ATTACHMENT="projects/${PROJECT_ID}/regions/${REGION}/networkAttachments/$(basename $(pwd))-psc-attachment"

uvx google-agents-cli deploy \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --service-account="${SA_EMAIL}" \
  --network-attachment="${PSC_ATTACHMENT}" \
  --agent-identity \
  --no-wait

# Check deployment progress
uvx google-agents-cli deploy --status
```

### Step 4: Post-Deployment Verification & Canary Promotion
```bash
# 1. Run evaluation suite using official agents-cli v1.5.0 flags
uvx google-agents-cli eval run \
  --dataset="tests/eval/datasets/smoke_dataset.json" \
  --metrics="response_match_score" \
  --agent="app"

# 2. Smoke test via A2A or ADK streaming protocol
uvx google-agents-cli run --url="https://YOUR_SERVICE_URL" --mode=adk "Hello, summarize what you can do."

# 3. [Cloud Run Only] Progressive canary traffic split (10% -> 50% -> 100%, or safe rollback to PREVIOUS_REVISION)
./scripts/canary_rollout.sh "$(basename $(pwd))" "${REGION}" "${PROJECT_ID}" "CANDIDATE_REVISION_NAME" canary-10
./scripts/canary_rollout.sh "$(basename $(pwd))" "${REGION}" "${PROJECT_ID}" "CANDIDATE_REVISION_NAME" promote-100
```
