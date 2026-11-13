# Agent Runtime Production Hardening Reference (`agent-runtime-hardening.md`, v2.1 — 15 Core Items)

## 1. 4 `MANAGED_NATIVE` Items vs. 11 Active Patches
On `agent_runtime` (`google_vertex_ai_reasoning_engine.app`):
- **4 Platform-Managed Items (`🟢 MANAGED_NATIVE`)**:
  - `C1.1`: Reasoning Engine IaC & `lifecycle.ignore_changes`
  - `C1.2`: Built-in container readiness/liveness health management
  - `C2.1`: Built-in Agent Platform Managed Session Service
  - `C5.1`: Built-in OpenTelemetry Cloud Trace collection (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY="true"`)
- **11 Active Hardening Items**:
  - `C2.2`: Cross-session Memory Bank (`VertexAiMemoryBankService` + `auto_save_session_to_memory` callback)
  - `C3.1`: Private VPC Egress via PSC Interface (`psc_interface_config` in `service.tf` + `google_compute_network_attachment` in `production_ready.tf`)
  - `C4.1`: Least-privilege SA (`roles/storage.objectUser`, strip unused `roles/cloudsql.client`)
  - `C4.2`: **Agent Identity (SPIFFE ID) & Agent Registry** (`spec.identity_type = "AGENT_IDENTITY"` in `service.tf` — mutually exclusive with `service_account` — plus `agentregistry.googleapis.com` & `apphub.googleapis.com` in `apis.tf`)
  - `C4.3`: Non-root `Dockerfile` (`USER 10001:10001`)
  - `C4.4`: **Agent Gateway (`AGENT_TO_ANYWHERE`) + UAP V2 `DRY_RUN`** (`google_network_services_agent_gateway`, `google_network_services_authz_extension` with `iapPolicyVersion = "V2"` and `iamEnforcementMode = var.agent_gateway_enforcement_mode` defaulting to `"DRY_RUN"` to prevent HTTP 498 outages, `google_network_security_authz_policy` in `production_ready.tf`; `agent_gateway_config` in `service.tf`; `AGENT_GATEWAY_ROOT_CERTIFICATES` CA bundle in `Dockerfile` prior to `USER 10001:10001`). *Note: `agent_gateway_config` in HCL requires a recent `google-beta` Terraform provider; if an older provider rejects the block, bind via `agents-cli deploy` or the Python SDK.*
  - `C5.2`: `NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS="false"`
  - `C5.3`: `alerts.tf` targeting `aiplatform.googleapis.com/ReasoningEngine` (P95 Latency + PromQL 5xx Error Ratio)
  - `C6.1`: `CostTokenGuardrailPlugin` (per-turn `invocation_id` reset + opt-in `max_output_tokens=0` for Thinking model safety)
  - `C6.2`: `GOOGLE_CLOUD_LOCATION` (default `global` DSQ) + Fail-Fast `HttpRetryOptions` (preserves `attempts=3`)
  - `C7.1`: Official `uvx google-agents-cli eval run` CI/CD gate in `.cloudbuild/staging.yaml`
