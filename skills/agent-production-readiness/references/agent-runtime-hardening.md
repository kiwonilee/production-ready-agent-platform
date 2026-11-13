# Agent Runtime (Agent Platform Managed Engine) Hardening Reference (`agent_runtime`, v2)

## 1. Platform-Managed (`MANAGED_NATIVE`) vs. Required Patches

When deploying to **Agent Runtime** (`google_vertex_ai_reasoning_engine.app`), the Agent Platform control plane natively handles **6 of the 17 checklist items** (`43.2 / 100.0` baseline score):

### 6 Natively Managed Items (`🟢 MANAGED_NATIVE`)
- **`C1.1`**: `google_vertex_ai_reasoning_engine.app` resource primitive
- **`C1.2`**: Platform-managed container health & lifecycle probes *(user `min_instances`/`cpu`/`memory` preserved untouched)*
- **`C2.1`**: Built-in **Agent Platform Managed Session Service (`VertexAiSessionService`)** *(no Redis/Cloud SQL needed; Self-Hosted options are optional)*
- **`C3.2`**: Zero-Trust IAM-authenticated endpoint (no public unauthenticated URL; no Cloud Armor LB needed)
- **`C5.1`**: Managed OpenTelemetry integration (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY = "true"`)
- **`C7.2`**: Platform deployment lifecycle (`lifecycle.ignore_changes` on `spec[0].container_spec`, `source_code_spec`, `deployment_spec` — Cloud Run `canary_rollout.sh` is **not** generated for Agent Runtime)

### 11 Items Patched by `prod-ready` to Reach `100.0 / 100.0`
1. **`C2.2` (`app/app_utils/services.py`, `app/agent.py`)**: Wire **Agent Platform Memory Bank (`VertexAiMemoryBankService` + `shared://memory`)** + `after_agent_callback` (`auto_save_session_to_memory`)
2. **`C2.3` (`deployment/terraform/storage.tf`)**: GCS artifact bucket `versioning` & 7-day `soft_delete_policy`
3. **`C3.1` (`deployment/terraform/production_ready.tf`, `service.tf`)**: Private Service Connect Interface (`google_compute_network_attachment.agent_psc_interface` + `/28` PGA subnet) wired directly into `psc_interface_config { network_attachment = ... }` inside `google_vertex_ai_reasoning_engine.app`
4. **`C4.1` (`deployment/terraform/variables.tf`)**: Replace `roles/storage.admin` with `roles/storage.objectUser` and strip unused `roles/cloudsql.client`
5. **`C4.2` (`app/app_utils/model_armor_plugin.py`, `app/agent.py`)**: Attach `ModelArmorGuardrailPlugin` (`google.cloud.modelarmor_v1` API + local injection/PII fallback + `before_tool_callback` SSRF/IDOR guard)
6. **`C4.3` (`Dockerfile`)**: Add non-root user `USER 10001:10001`
7. **`C5.2` (`deployment/terraform/service.tf`, `app/agent.py`)**: Enforce `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="NO_CONTENT"` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS="false"` (+ opt-in `BigQueryAgentAnalyticsPlugin` in `US`)
8. **`C5.3` (`deployment/terraform/alerts.tf`)**: Agent Runtime-specific `aiplatform.googleapis.com/ReasoningEngine` latency/error metrics + Guardrail log alert (zero `cloud_run_revision` references!)
9. **`C6.1` (`app/app_utils/cost_guard_plugin.py`, `budget.tf`)**: Per-turn `invocation_id` loop termination + `max_output_tokens` cap + optional `budget.tf`
10. **`C6.2` (`app/agent.py`, `service.tf`)**: 5-attempt exponential backoff on 429 + `GOOGLE_CLOUD_LOCATION` (`"global"` or regional residency)
11. **`C7.1` (`.cloudbuild/staging.yaml`, `tests/eval/eval_config.yaml`, `tests/eval/datasets/smoke_dataset.json`)**: Official `uvx google-agents-cli eval run --dataset ... --metrics ...` CI/CD quality gate
