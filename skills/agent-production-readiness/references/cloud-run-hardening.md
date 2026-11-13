# Cloud Run Production Hardening Reference (`cloud-run-hardening.md`, v2 — 14 Core Items)

## 1. Zero Over-Reaching Mutations
- **Compute Sizing Preserved**: Existing `cpu`, `memory`, `max_instance_request_concurrency`, and `min_instance_count` in `deployment/terraform/service.tf` are preserved untouched. Tune sizing after running `tests/load_test/` (Locust).
- **Ingress & Deletion Protection Untouched**: The patcher never locks out `*.a.run.app` (`default_uri_disabled`) or forces GCLB/Cloud Armor WAF/Billing Budget/deletion protection mutations. Inbound auth/edge topology (`--allow-unauthenticated` + app OAuth/JWT vs IAM Invoker vs IAP vs GCLB+WAF) is chosen per service architecture.

## 2. Key Patched Files on Cloud Run
- `deployment/terraform/service.tf`: Startup & liveness probes (`/list-apps:8080`), `startup_cpu_boost = true`, `EXECUTION_ENVIRONMENT_GEN2`, `GOOGLE_CLOUD_AGENT_ENGINE_ID`, `NO_CONTENT` & `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS = "false"`, and optional `vpc_access` (`var.vpc_network_name` / `var.vpc_subnet_name`).
- `deployment/terraform/variables.tf`: Least-privilege `roles/storage.objectUser` and `roles/modelarmor.user`.
- `deployment/terraform/alerts.tf`: Cloud Run PromQL 5xx error ratio (`> 2%`), P95 latency (`> 15000ms`), and Model Armor block spike alerts.
- `app/app_utils/services.py`, `model_armor_plugin.py`, `cost_guard_plugin.py`, and `app/agent.py`.
