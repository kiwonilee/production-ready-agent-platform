# Agent Runtime Production Hardening Reference (`agent-runtime-hardening.md`, v2 — 14 Core Items)

## 1. 4 `MANAGED_NATIVE` Items vs. 10 Active Patches
On `agent_runtime` (`google_vertex_ai_reasoning_engine.app`):
- **4 Platform-Managed Items (`🟢 MANAGED_NATIVE`)**:
  - `C1.1`: Reasoning Engine IaC & `lifecycle.ignore_changes`
  - `C1.2`: Built-in container readiness/liveness health management
  - `C2.1`: Built-in Agent Platform Managed Session Service
  - `C5.1`: Built-in OpenTelemetry Cloud Trace collection (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY="true"`)
- **10 Active Hardening Items**:
  - `C2.2`: Cross-session Memory Bank (`VertexAiMemoryBankService` + `auto_save_session_to_memory` callback)
  - `C3.1`: Private VPC Egress via PSC Interface (`psc_interface_config` in `service.tf` + `google_compute_network_attachment` in `production_ready.tf`)
  - `C4.1`: Least-privilege SA (`roles/storage.objectUser`, strip unused `roles/cloudsql.client`)
  - `C4.2`: `ModelArmorGuardrailPlugin` (`modelarmor_v1` API + PII fallback + `before_tool_callback` SSRF/IDOR)
  - `C4.3`: Non-root `Dockerfile` (`USER 10001:10001`)
  - `C5.2`: `NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS="false"`
  - `C5.3`: `alerts.tf` targeting `aiplatform.googleapis.com/ReasoningEngine`
  - `C6.1`: `CostTokenGuardrailPlugin` (per-turn `invocation_id` reset + `max_output_tokens` cap)
  - `C6.2`: `HttpRetryOptions(attempts=5)` + `GOOGLE_CLOUD_LOCATION`
  - `C7.1`: Official `uvx google-agents-cli eval run` CI/CD gate in `.cloudbuild/staging.yaml`
