# Cloud Run Production Hardening Reference (`cloud-run-hardening.md`, v2.1 — 15 Core Items)

## 1. Zero Over-Reaching Mutations & Staged Governance
- **Compute Sizing Preserved**: Existing `cpu`, `memory`, `max_instance_request_concurrency`, and `min_instance_count` in `deployment/terraform/service.tf` are preserved untouched. Tune sizing after running `tests/load_test/` (Locust).
- **Agent Identity & Registry (`C4.2` Active)**: Enables `agentregistry.googleapis.com` & `apphub.googleapis.com` in `apis.tf` and configures `functional_type = "FUNCTIONAL_TYPE_AGENT"` + `identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"` in `service.tf` (cleanly resolving mutual exclusivity with `service_account`).
- **Agent Gateway Staged Adoption (`C4.4` Comment-Only Advisory)**: Does **not** generate `production_ready.tf`, `google_network_services_agent_gateway`, or `AGENT_GATEWAY_ROOT_CERTIFICATES` on Cloud Run. Instead injects a clear architectural comment into `service.tf`: *"Cloud Run은 Direct VPC Egress + SWP/VPC-SC + IAP + Model Armor라는 검증된 GA 인프라 보안 위에 Agent Identity / Registry / Self-Managed AGW를 단계적으로 얹는 접근이 가장 현실적입니다."*
- **Ingress & Deletion Protection Untouched**: The patcher never locks out `*.a.run.app` (`default_uri_disabled`), never forces GCLB/Cloud Armor WAF/Billing Budget/deletion protection mutations, and never forces Model Armor plugins into individual agent code.
- **Retry-Storm & Thinking-Model Safe**: Preserves `agents-cli`'s Fail-Fast `HttpRetryOptions(attempts=3)` and defaults `max_output_tokens=0` (opt-in) so Gemini 2.5/3.x Thinking models aren't truncated.

## 2. Key Patched Files on Cloud Run
- `deployment/terraform/apis.tf`: Enables `agentregistry.googleapis.com` and `apphub.googleapis.com`.
- `deployment/terraform/service.tf`: `functional_type = "FUNCTIONAL_TYPE_AGENT"`, `identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"`, `# [C4.4 Advisory]` staged-adoption architectural comment, Startup & liveness probes (`/list-apps:8080`), `startup_cpu_boost = true`, `EXECUTION_ENVIRONMENT_GEN2`, `GOOGLE_CLOUD_AGENT_ENGINE_ID`, `NO_CONTENT` & `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS = "false"`, `GOOGLE_CLOUD_LOCATION`, and optional `vpc_access` (`var.vpc_network_name` / `var.vpc_subnet_name`).
- `deployment/terraform/variables.tf`: Least-privilege `roles/storage.objectUser`.
- `deployment/terraform/alerts.tf`: Cloud Run PromQL 5xx error ratio (`> 2%`) and P95 latency (`> 15000ms`) alerts.
- `app/app_utils/services.py`, `cost_guard_plugin.py`, and `app/agent.py`.
