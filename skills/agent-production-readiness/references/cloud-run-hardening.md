# Cloud Run Production Hardening Reference (`cloud_run`, v2)

## 1. Compatibility Contract with `google-agents-cli` v1.5.0

When hardening a project scaffolded with `agents-cli scaffold create --deployment-target cloud_run` (or scaffolding a pure ADK prototype for Cloud Run), **preserve these exact resource identifiers** and **never force-upsize user compute sizing (`cpu`, `memory`, `concurrency`, `min_instance_count`)**:

| File | Required Identifier | Production Hardening Applied (v2) |
| :--- | :--- | :--- |
| `deployment/terraform/service.tf` | `resource "google_cloud_run_v2_service" "app"` | `deletion_protection = true`, `startup_cpu_boost = true`, `startup_probe` & `liveness_probe` (`/list-apps:8080`), `EXECUTION_ENVIRONMENT_GEN2`, `vpc_access` (`PRIVATE_RANGES_ONLY`), `GOOGLE_CLOUD_AGENT_ENGINE_ID`, `NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`, ingress (`INTERNAL_LOAD_BALANCER` + `default_uri_disabled = true` OR `INTERNAL_ONLY` for `internal_iap`) |
| `deployment/terraform/variables.tf` | `variable "app_sa_roles"` | Replace `"roles/storage.admin"` with `"roles/storage.objectUser"`; add `var.vpc_network_name` & `var.agent_engine_id` |
| `deployment/terraform/storage.tf` | `resource "google_storage_bucket" "logs_data_bucket"` | Enable `versioning` and 7-day `soft_delete_policy` |
| `app/app_utils/services.py` | `SESSION_SERVICE_URI` & `MEMORY_SERVICE_URI` | Wire **Agent Platform Managed Sessions (`VertexAiSessionService`)** & **Memory Bank (`VertexAiMemoryBankService`)** with zero DB operations burden *(optional self-hosted Cloud SQL / Redis / Vector DB documented in comments)* |
| `app/fast_api_app.py` | `get_fast_api_app(...)` | Ensure `session_service_uri`, `memory_service_uri`, and `otel_to_cloud=True` are wired cleanly |

---

## 2. Compute Sizing Policy (Load-Test Driven, Never Auto-Upsized)

`agent-production-readiness` v2 **deliberately preserves** your existing `cpu`, `memory`, `max_instance_request_concurrency`, and `min_instance_count` in `service.tf`.
Run Locust (`tests/load_test/load_test.py`) against your staging endpoint under realistic concurrency and token sizes before adjusting compute limits or enabling `min_instance_count >= 1` for cold-start SLA.

---

## 3. Managed Session & Memory Bank vs. Self-Hosted Option

- **Default (Managed — Recommended)**: Cloud Run connects to **Agent Platform Managed Sessions (`VertexAiSessionService`)** and **Memory Bank (`VertexAiMemoryBankService`)** via `GOOGLE_CLOUD_AGENT_ENGINE_ID`, eliminating Cloud SQL HA patching, VPC connection-pool freezes (`cpu_idle = false`), and Memorystore Redis idle costs.
- **Optional Self-Hosted Path**: If regulatory policy or existing infrastructure requires self-hosting, you may independently provision Cloud SQL (`REGIONAL` HA + PITR + `cpu_idle = false` for `asyncpg` pooling) / Memorystore Redis / AlloyDB `pgvector` and point `SESSION_SERVICE_URI` / `MEMORY_SERVICE_URI` accordingly.

---

## 4. Drop-In Terraform Files Added to `deployment/terraform/`

1. **`production_ready.tf`**:
   - `[C3.1]` `data.google_compute_network.agent_vpc` (parameterized via `var.vpc_network_name`) + `google_compute_subnetwork.agent_direct_vpc_subnet` (`/26` CIDR with `private_ip_google_access = true`)
   - `[C3.2 · external_waf]` `google_compute_security_policy.agent_edge_waf` (Cloud Armor IP rate limit + OWASP SQLi/XSS) wired cleanly to `google_compute_region_network_endpoint_group.agent_serverless_neg` + `google_compute_backend_service.agent_edge_backend` (zero orphaned WAF policies!)
2. **`alerts.tf`**:
   - P95 Latency > `15,000ms` (configurable via `latency_p95_ms`, LLM-appropriate default)
   - PromQL 5xx Error Ratio > `2%` (`condition_prometheus_query_language`)
   - Model Armor Guardrail Block Spike > `10 / 5m`
3. **`budget.tf`** (optional — activates only when `var.billing_account_id != ""`):
   - `google_billing_budget.agent_monthly_budget` (50% / 80% / 100%) + `google_pubsub_topic.budget_circuit_breaker`
4. **`scripts/canary_rollout.sh`** (`C7.2`):
   - `canary-10` → `canary-50` → `promote-100` → safe `rollback` to resolved `$PREVIOUS_REVISION` (never `--to-latest`!).
