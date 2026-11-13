# Cloud Run Production Hardening Reference (`cloud-run-hardening.md`, v2 — 13 Core Items)

## 1. Zero Over-Reaching Mutations
- **Compute Sizing Preserved**: Existing `cpu`, `memory`, `max_instance_request_concurrency`, and `min_instance_count` in `deployment/terraform/service.tf` are preserved untouched. Tune sizing after running `tests/load_test/` (Locust).
- **Ingress, Governance & Deletion Protection Untouched**: The patcher never locks out `*.a.run.app` (`default_uri_disabled`), never forces GCLB/Cloud Armor WAF/Billing Budget/deletion protection mutations, and never forces Model Armor plugins into individual agent code. Inbound auth/edge topology, centralized AI governance (**Agent Gateway + Model Armor Floor Settings**), and production SLA (**Provisioned Throughput / Priority PayGo**) are surfaced as non-blocking advisories.
- **Retry-Storm & Thinking-Model Safe**: Preserves `agents-cli`'s Fail-Fast `HttpRetryOptions(attempts=3)` and defaults `max_output_tokens=0` (opt-in) so Gemini 2.5/3.x Thinking models aren't truncated.

## 2. Key Patched Files on Cloud Run
- `deployment/terraform/service.tf`: Startup & liveness probes (`/list-apps:8080`), `startup_cpu_boost = true`, `EXECUTION_ENVIRONMENT_GEN2`, `GOOGLE_CLOUD_AGENT_ENGINE_ID`, `NO_CONTENT` & `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS = "false"`, `GOOGLE_CLOUD_LOCATION`, and optional `vpc_access` (`var.vpc_network_name` / `var.vpc_subnet_name`).
- `deployment/terraform/variables.tf`: Least-privilege `roles/storage.objectUser`.
- `deployment/terraform/alerts.tf`: Cloud Run PromQL 5xx error ratio (`> 2%`) and P95 latency (`> 15000ms`) alerts.
- `app/app_utils/services.py`, `cost_guard_plugin.py`, and `app/agent.py`.
