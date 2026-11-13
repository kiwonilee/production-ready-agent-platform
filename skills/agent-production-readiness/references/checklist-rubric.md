# 7-Domain, 17-Item Production-Readiness Checklist & Scoring Rubric (`C1.1` – `C7.2`, v2)

## 1. Domain Weighting Formula

Each checklist item is evaluated deterministically as:
- `GREEN` = `100` points (Pass, or `MANAGED_NATIVE` on Agent Runtime)
- `AMBER` = `50` points (Minor gap / non-blocking warning)
- `RED` = `0` points (Critical production blocker)

> **Non-Scoring Advisories**: Compute sizing (`cpu`, `memory`, `concurrency`, `min_instances`) is never auto-upsized or penalized in the score; load-test sizing guidance is surfaced separately as `advisories`.

$$\text{Weighted Readiness Score} = \frac{\sum_{d=1}^{7} (\text{Domain Score}_d \times \text{Weight}_d)}{\sum_{d=1}^{7} \text{Weight}_d}$$

| Domain ID | Domain Name | Weight | Rationale |
| :---: | :--- | :---: | :--- |
| **D1** | **Runtime & Scaling** | `2x` | IaC resource primitives (`C1.1`), health probes & `startup_cpu_boost` (`C1.2`) without forced sizing |
| **D2** | **Session & Memory** | `2x` | **Managed-first** `VertexAiSessionService` (`C2.1`), `VertexAiMemoryBankService` (`C2.2`), GCS durability (`C2.3`) |
| **D3** | **Private Networking & Edge** | `2x` | Direct VPC / PSC-I private egress + PGA (`C3.1`), Zero-Trust Ingress + WAF or `internal_iap` (`C3.2`) |
| **D4** | **Security, IAM & Guardrails** | `3x` | Highest enterprise blocker: Least-privilege IAM (`C4.1`), Model Armor API + SSRF/IDOR (`C4.2`), Non-root Gen2 (`C4.3`) |
| **D5** | **Observability & Audit** | `1x` | OpenTelemetry GenAI spans (`C5.1`), `NO_CONTENT` privacy (`C5.2`), target-specific SRE Alerts (`C5.3`) |
| **D6** | **FinOps & Resilience** | `1x` | Per-turn `invocation_id` loop cap + `max_output_tokens` + budget (`C6.1`), 429 DSQ backoff (`C6.2`) |
| **D7** | **CI/CD & Eval Gate** | `2x` | Official `agents-cli eval run` CI/CD gate (`C7.1`) + Cloud Run canary / previous-revision rollback (`C7.2`) |

### Gate Verdicts
- **🟢 Production Ready**: `Weighted Score >= 90.0` AND `RED == 0`
- **🟡 Ready with Follow-ups**: `70.0 <= Weighted Score < 90.0` AND `RED == 0`
- **🔴 Not Production Ready**: `Weighted Score < 70.0` OR `RED >= 1`

---

## 2. 17-Item Cross-Runtime Matrix (`Cloud Run` vs. `Agent Runtime` vs. `GKE`)

| ID | Alias | Title | Severity | Cloud Run (`google_cloud_run_v2_service`) | Agent Runtime (`google_vertex_ai_reasoning_engine`) | GKE (`Reference`) |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- |
| **C1.1** | `REL-00` | Runtime Resource Primitive | `CRITICAL` | `google_cloud_run_v2_service.app` | 🟢 **MANAGED_NATIVE** (`google_vertex_ai_reasoning_engine.app`) | `Deployment` + `Service` |
| **C1.2** | `REL-01` | Health Probes & Startup CPU Boost | `HIGH` | `startup_probe` & `liveness_probe` (`/list-apps:8080`) + `startup_cpu_boost = true` *(preserves user CPU/mem/min)* | 🟢 **MANAGED_NATIVE** (Platform health & lifecycle probes) | K8s `startupProbe`, `readinessProbe`, `livenessProbe` |
| **C2.1** | `MEM-01` | Persistent Multi-Turn Sessions | `CRITICAL` | **Managed**: `VertexAiSessionService` + `GOOGLE_CLOUD_AGENT_ENGINE_ID` *(Optional self-hosted: Cloud SQL/Redis)* | 🟢 **MANAGED_NATIVE** (Built-in Managed Session Service) | Managed Sessions or Cloud SQL + Redis |
| **C2.2** | `MEM-02` | Cross-Session Memory Bank | `HIGH` | **Managed**: `VertexAiMemoryBankService` (`shared://memory`) + `after_agent_callback` *(Optional self-hosted: Vector DB)* | Same (`VertexAiMemoryBankService` + `after_agent_callback`) | Managed Memory Bank or AlloyDB `pgvector` |
| **C2.3** | `MEM-03` | GCS Artifact Bucket Durability | `MEDIUM` | `google_storage_bucket.logs_data_bucket` (`versioning` + 7d `soft_delete_policy`) | Same (`versioning` + `soft_delete_policy`) | Same (`GcsArtifactService`) |
| **C3.1** | `NET-01` | Private VPC Egress & PGA *(incl. old C3.3)* | `CRITICAL` | Direct VPC Egress (`PRIVATE_RANGES_ONLY`) + `/26` subnet (`private_ip_google_access = true`) | `google_compute_network_attachment` (PSC-I) wired into `psc_interface_config` + `/28` PGA subnet | Private GKE Nodes + Cloud NAT + PGA |
| **C3.2** | `NET-02` | Zero-Trust Ingress & WAF / IAP | `CRITICAL` | `external_waf`: `INTERNAL_LOAD_BALANCER` + `default_uri_disabled` + Serverless NEG + Cloud Armor WAF · `internal_iap`: `INTERNAL_ONLY` | 🟢 **MANAGED_NATIVE** (IAM-only Endpoint, no public URL) | GKE `Gateway` + Cloud Armor + IAP |
| **C4.1** | `SEC-01` | Least-Privilege SA & Secret Pinning | `CRITICAL` | `roles/storage.objectUser` (drop `storage.admin`), `deletion_protection = true`, pinned Secret version | `roles/storage.objectUser` (drop `storage.admin` & unused `cloudsql.client`) | Workload Identity Federation + CSI |
| **C4.2** | `SEC-02` | Model Armor API & Tool SSRF/IDOR *(incl. old C4.4)* | `CRITICAL` | `ModelArmorGuardrailPlugin` (`google.cloud.modelarmor_v1` + PII fallback + `before_tool_callback` SSRF/IDOR) | Same (`ModelArmorGuardrailPlugin` registered in `app/agent.py`) | Same + GKE `NetworkPolicy` |
| **C4.3** | `SEC-03` | Non-Root Container & Gen2 Sandbox | `HIGH` | `Dockerfile` `USER 10001:10001` + `EXECUTION_ENVIRONMENT_GEN2` | `Dockerfile` `USER 10001:10001` | `runAsNonRoot: true` + `gVisor` |
| **C5.1** | `OBS-01` | OpenTelemetry GenAI Tracing | `HIGH` | `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` + `otel_to_cloud=True` | 🟢 **MANAGED_NATIVE** (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`) | GKE Managed Prometheus + OTel Collector |
| **C5.2** | `OBS-02` | Privacy-Preserving Audit & Opt-In BQ | `CRITICAL` | `NO_CONTENT` + `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` (+ opt-in `BigQueryAgentAnalyticsPlugin` in `US`) | Same (`service.tf` privacy envs + opt-in BQ) | Same |
| **C5.3** | `OBS-03` | Target-Specific SRE Golden Alerts | `HIGH` | `alerts.tf`: `cloud_run_revision` P95 > `15s`, PromQL 5xx ratio > `2%`, Guardrail spike | `alerts.tf`: `aiplatform.googleapis.com/ReasoningEngine` metrics + Guardrail spike | PromQL `google_monitoring_alert_policy` |
| **C6.1** | `FIN-01` | Per-Turn Loop Cap & Monthly Budget | `HIGH` | `CostTokenGuardrailPlugin` (`invocation_id` reset + `max_output_tokens`) + optional `budget.tf` | Same (`CostTokenGuardrailPlugin` + optional `budget.tf`) | Same + GKE `ResourceQuota` |
| **C6.2** | `FIN-02` | 429 DSQ Quota Resilience | `HIGH` | `HttpRetryOptions(attempts=5, initial_delay=1.0, exp_base=2.0)` + `GOOGLE_CLOUD_LOCATION` | Same | Same |
| **C7.1** | `OPS-01` | CI/CD Eval Quality Gate | `CRITICAL` | `.cloudbuild/staging.yaml` `uvx google-agents-cli eval run --dataset ... --metrics ...` + `eval_config.yaml` + `smoke_dataset.json` | Same | Same |
| **C7.2** | `OPS-02` | Canary Rollout & Safe Rollback | `HIGH` | `lifecycle.ignore_changes` + `scripts/canary_rollout.sh` (`10% -> 50% -> 100%`, rolls back to `$PREVIOUS_REVISION`) | 🟢 **MANAGED_NATIVE** (`lifecycle.ignore_changes` preserved; no Cloud Run canary script) | Argo Rollouts / Gateway weight split |
