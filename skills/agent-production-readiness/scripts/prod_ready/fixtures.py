"""Ground-Truth Sample Project Generators for Demo & Automated Verification (Checklist v3 baselines).

Creates exact replicas of:
1. Mode A: Pure ADK Prototype (`adk create` / `agents-cli scaffold create --prototype`)
2. Mode B (Cloud Run): `agents-cli` v1.5.0 Cloud Run scaffolded project (As-Is defaults)
3. Mode B (Agent Runtime): `agents-cli` v1.5.0 Agent Runtime scaffolded project (As-Is defaults)
4. Mode B (GKE): `agents-cli` v1.5.0 GKE Autopilot scaffolded project (As-Is defaults)
"""

from __future__ import annotations

from pathlib import Path


def create_prototype_fixture(dest: Path, name: str = "proto-support-agent") -> Path:
    """Creates a Mode A Pure Prototype project (no Terraform, no Dockerfile, no CI/CD)."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    app_dir = dest / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "agent.py").write_text(
        '''from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

MODEL = "gemini-2.5-flash"


def get_weather(query: str) -> str:
    return "It's 60 degrees and foggy."


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="You are a helpful enterprise assistant.",
    tools=[get_weather],
)

app = App(
    root_agent=root_agent,
    name="app",
)
''',
        encoding="utf-8",
    )
    return dest


def create_agents_cli_cloud_run_fixture(dest: Path, name: str = "cr-enterprise-agent") -> Path:
    """Creates an exact baseline replica of an `agents-cli` v1.5.0 Cloud Run project."""
    create_prototype_fixture(dest, name=name)
    tf_dir = dest / "deployment" / "terraform"
    tf_dir.mkdir(parents=True, exist_ok=True)

    (tf_dir / "variables.tf").write_text(
        f'''variable "project_name" {{
  type    = string
  default = "{name}"
}}

variable "project_id" {{
  type = string
}}

variable "region" {{
  type    = string
  default = "us-east1"
}}

variable "app_sa_roles" {{
  type = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/secretmanager.secretAccessor",
  ]
}}
''',
        encoding="utf-8",
    )

    (tf_dir / "storage.tf").write_text(
        '''resource "google_storage_bucket" "logs_data_bucket" {
  name                        = "${var.project_id}-${var.project_name}-logs"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  depends_on = [resource.google_project_service.services]
}
''',
        encoding="utf-8",
    )

    (tf_dir / "service.tf").write_text(
        f'''resource "google_cloud_run_v2_service" "app" {{
  name                = var.project_name
  location            = var.region
  project             = var.project_id
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels = {{
    "created-by" = "agents-cli"
  }}

  template {{
    containers {{
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      env {{
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }}

      env {{
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }}

      env {{
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "True"
      }}

      resources {{
        limits = {{
          cpu    = "1"
          memory = "4Gi"
        }}
      }}

      env {{
        name  = "LOGS_BUCKET_NAME"
        value = google_storage_bucket.logs_data_bucket.name
      }}

      env {{
        name  = "OTEL_SERVICE_NAME"
        value = "{name}"
      }}

      env {{
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }}

      env {{
        name  = "OTEL_SEMCONV_STABILITY_OPT_IN"
        value = "gen_ai_latest_experimental"
      }}
    }}

    service_account                  = google_service_account.app_sa.email
    max_instance_request_concurrency = 8

    scaling {{
      min_instance_count = 0
      max_instance_count = 10
    }}

    session_affinity = true
  }}

  lifecycle {{
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }}
}}
''',
        encoding="utf-8",
    )

    (dest / "Dockerfile").write_text(
        '''FROM python:3.12-slim
WORKDIR /code
COPY . .
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
''',
        encoding="utf-8",
    )

    app_utils = dest / "app" / "app_utils"
    app_utils.mkdir(parents=True, exist_ok=True)
    (app_utils / "services.py").write_text(
        '''SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"


def get_session_service():
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    return InMemorySessionService()
''',
        encoding="utf-8",
    )
    (dest / "app" / "fast_api_app.py").write_text(
        '''otel_to_cloud = True
''',
        encoding="utf-8",
    )

    cb_dir = dest / ".cloudbuild"
    cb_dir.mkdir(parents=True, exist_ok=True)
    (cb_dir / "staging.yaml").write_text(
        '''steps:
  - name: "gcr.io/cloud-builders/gcloud"
    id: deploy-staging
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        uvx google-agents-cli deploy --project ${_STAGING_PROJECT_ID} --region ${_REGION}

  - name: "gcr.io/cloud-builders/gcloud"
    id: load_test
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        uvx locust==2.31.1 -f tests/load_test/load_test.py --headless -t 30s -u 10 -r 0.5

substitutions:
  _STAGING_PROJECT_ID: YOUR_STAGING_PROJECT_ID
  _REGION: us-east1
''',
        encoding="utf-8",
    )
    return dest


def create_agents_cli_agent_runtime_fixture(dest: Path, name: str = "ar-enterprise-agent") -> Path:
    """Creates an exact baseline replica of an `agents-cli` v1.5.0 Agent Runtime project."""
    create_prototype_fixture(dest, name=name)
    tf_dir = dest / "deployment" / "terraform"
    tf_dir.mkdir(parents=True, exist_ok=True)

    (tf_dir / "variables.tf").write_text(
        f'''variable "project_name" {{
  type    = string
  default = "{name}"
}}

variable "project_id" {{
  type = string
}}

variable "region" {{
  type    = string
  default = "us-east1"
}}

variable "app_sa_roles" {{
  type = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
  ]
}}
''',
        encoding="utf-8",
    )

    (tf_dir / "storage.tf").write_text(
        '''resource "google_storage_bucket" "logs_data_bucket" {
  name                        = "${var.project_id}-${var.project_name}-logs"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  depends_on = [resource.google_project_service.services]
}
''',
        encoding="utf-8",
    )

    (tf_dir / "service.tf").write_text(
        f'''resource "google_vertex_ai_reasoning_engine" "app" {{
  display_name = var.project_name
  description  = "Agent deployed via Terraform"
  region       = var.region
  project      = var.project_id

  spec {{
    agent_framework = "google-adk"
    service_account = google_service_account.app_sa.email

    deployment_spec {{
      min_instances         = 1
      max_instances         = 10
      container_concurrency = 9

      resource_limits = {{
        cpu    = "4"
        memory = "8Gi"
      }}

      env {{
        name  = "LOGS_BUCKET_NAME"
        value = google_storage_bucket.logs_data_bucket.name
      }}

      env {{
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }}

      env {{
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "True"
      }}

      env {{
        name  = "OTEL_SERVICE_NAME"
        value = "{name}"
      }}

      env {{
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }}

      env {{
        name  = "OTEL_SEMCONV_STABILITY_OPT_IN"
        value = "gen_ai_latest_experimental"
      }}

      env {{
        name  = "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"
        value = "true"
      }}
    }}
  }}

  lifecycle {{
    ignore_changes = [
      spec[0].container_spec,
      spec[0].source_code_spec,
      spec[0].deployment_spec,
    ]
  }}

  depends_on = [google_project_service.services]
}}
''',
        encoding="utf-8",
    )

    (dest / "Dockerfile").write_text(
        '''FROM python:3.12-slim
WORKDIR /code
COPY . .
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
''',
        encoding="utf-8",
    )

    app_utils = dest / "app" / "app_utils"
    app_utils.mkdir(parents=True, exist_ok=True)
    (app_utils / "services.py").write_text(
        '''SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"
''',
        encoding="utf-8",
    )

    cb_dir = dest / ".cloudbuild"
    cb_dir.mkdir(parents=True, exist_ok=True)
    (cb_dir / "staging.yaml").write_text(
        '''steps:
  - name: "gcr.io/cloud-builders/gcloud"
    id: deploy-staging
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        uvx google-agents-cli deploy --project ${_STAGING_PROJECT_ID} --region ${_REGION}

  - name: "gcr.io/cloud-builders/gcloud"
    id: load_test
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        uvx locust==2.31.1 -f tests/load_test/load_test.py --headless -t 30s -u 2 -r 0.5

substitutions:
  _STAGING_PROJECT_ID: YOUR_STAGING_PROJECT_ID
  _REGION: us-east1
''',
        encoding="utf-8",
    )
    return dest


def create_agents_cli_gke_fixture(dest: Path, name: str = "gke-enterprise-agent") -> Path:
    """Creates an exact baseline replica of an `agents-cli` v1.5.0 GKE Autopilot project."""
    create_prototype_fixture(dest, name=name)
    (dest / "agents-cli-manifest.yaml").write_text(
        f"""version: 1
create_params:
  project_name: {name}
  deployment_target: 'gke'
  session_type: in_memory
""",
        encoding="utf-8",
    )
    tf_dir = dest / "deployment" / "terraform"
    tf_dir.mkdir(parents=True, exist_ok=True)

    (tf_dir / "variables.tf").write_text(
        f'''variable "project_name" {{
  type    = string
  default = "{name}"
}}

variable "project_id" {{
  type = string
}}

variable "region" {{
  type    = string
  default = "us-east1"
}}

variable "app_sa_roles" {{
  type = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
  ]
}}
''',
        encoding="utf-8",
    )

    (tf_dir / "apis.tf").write_text(
        '''locals {
  services = [
    "aiplatform.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
    "telemetry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
  ]
}

resource "google_project_service" "services" {
  count              = length(local.services)
  project            = var.project_id
  service            = local.services[count.index]
  disable_on_destroy = false
}

resource "google_project_service_identity" "vertex_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "aiplatform.googleapis.com"
}
''',
        encoding="utf-8",
    )

    (tf_dir / "iam.tf").write_text(
        '''data "google_project" "project" {
  project_id = var.project_id
}

resource "google_service_account" "app_sa" {
  account_id   = "${var.project_name}-app"
  display_name = "${var.project_name} Agent Service Account"
  project      = var.project_id
  depends_on   = [resource.google_project_service.services]
}

resource "google_project_iam_member" "app_sa_roles" {
  for_each   = toset(var.app_sa_roles)
  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:${google_service_account.app_sa.email}"
  depends_on = [resource.google_project_service.services]
}
''',
        encoding="utf-8",
    )

    (tf_dir / "providers.tf").write_text(
        '''terraform {
  required_version = ">= 1.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.28.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.37.0"
    }
  }
}

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.app.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.app.master_auth[0].cluster_ca_certificate)
}
''',
        encoding="utf-8",
    )

    (tf_dir / "storage.tf").write_text(
        '''resource "google_storage_bucket" "logs_data_bucket" {
  name                        = "${var.project_id}-${var.project_name}-logs"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  depends_on = [resource.google_project_service.services]
}
''',
        encoding="utf-8",
    )

    (tf_dir / "service.tf").write_text(
        f'''# VPC Network
resource "google_compute_network" "gke_network" {{
  name                    = "${{var.project_name}}-network"
  project                 = var.project_id
  auto_create_subnetworks = false

  depends_on = [resource.google_project_service.services]
}}

# Subnet for GKE cluster
resource "google_compute_subnetwork" "gke_subnet" {{
  name          = "${{var.project_name}}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.gke_network.id
  ip_cidr_range = "10.0.0.0/20"
}}

# GKE Autopilot Cluster
resource "google_container_cluster" "app" {{
  name     = var.project_name
  location = var.region
  project  = var.project_id

  network    = google_compute_network.gke_network.name
  subnetwork = google_compute_subnetwork.gke_subnet.name

  enable_autopilot = true

  private_cluster_config {{
    enable_private_nodes    = true
    enable_private_endpoint = false
  }}

  ip_allocation_policy {{}}

  deletion_protection = false

  depends_on = [
    resource.google_project_service.services,
  ]
}}

# Cloud Router & NAT for private GKE nodes
resource "google_compute_router" "router" {{
  name    = "${{var.project_name}}-router"
  project = var.project_id
  region  = var.region
  network = google_compute_network.gke_network.id
}}

resource "google_compute_router_nat" "nat" {{
  name                               = "${{var.project_name}}-nat"
  project                            = var.project_id
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}}

# Artifact Registry for container images
resource "google_artifact_registry_repository" "docker_repo" {{
  location      = var.region
  repository_id = var.project_name
  format        = "DOCKER"
  project       = var.project_id

  depends_on = [resource.google_project_service.services]
}}

# Workload Identity binding (KSA -> GSA)
resource "google_service_account_iam_member" "workload_identity_binding" {{
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${{var.project_id}}.svc.id.goog[${{var.project_name}}/${{var.project_name}}]"

  depends_on = [google_container_cluster.app]
}}

# --- Kubernetes Resources ---

resource "kubernetes_namespace_v1" "app" {{
  metadata {{
    name = var.project_name
  }}
  depends_on = [google_container_cluster.app]
}}

resource "kubernetes_service_account_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    annotations = {{
      "iam.gke.io/gcp-service-account" = google_service_account.app_sa.email
    }}
  }}
}}

resource "kubernetes_service_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {{
      app = var.project_name
    }}
    annotations = {{
      "cloud.google.com/load-balancer-type" = "Internal"
    }}
  }}
  spec {{
    type = "LoadBalancer"
    port {{
      port        = 8080
      target_port = 8080
      protocol    = "TCP"
    }}
    selector = {{
      app = var.project_name
    }}
  }}
}}

resource "kubernetes_horizontal_pod_autoscaler_v2" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {{
      app = var.project_name
    }}
  }}
  spec {{
    scale_target_ref {{
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = var.project_name
    }}
    min_replicas = 2
    max_replicas = 10
    metric {{
      type = "Resource"
      resource {{
        name = "cpu"
        target {{
          type                = "Utilization"
          average_utilization = 70
        }}
      }}
    }}
  }}
}}

resource "kubernetes_pod_disruption_budget_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {{
      app = var.project_name
    }}
  }}
  spec {{
    min_available = 1
    selector {{
      match_labels = {{
        app = var.project_name
      }}
    }}
  }}
}}

resource "kubernetes_deployment_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {{
      app = var.project_name
    }}
  }}

  spec {{
    selector {{
      match_labels = {{
        app = var.project_name
      }}
    }}

    template {{
      metadata {{
        labels = {{
          app = var.project_name
        }}
      }}

      spec {{
        service_account_name = kubernetes_service_account_v1.app.metadata[0].name

        container {{
          name  = var.project_name
          image = "us-docker.pkg.dev/cloudrun/container/hello"

          port {{
            container_port = 8080
            protocol       = "TCP"
          }}

          env {{
            name  = "LOGS_BUCKET_NAME"
            value = google_storage_bucket.logs_data_bucket.name
          }}

          env {{
            name  = "OTEL_SERVICE_NAME"
            value = "{name}"
          }}

          env {{
            name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
            value = "NO_CONTENT"
          }}

          env {{
            name  = "OTEL_SEMCONV_STABILITY_OPT_IN"
            value = "gen_ai_latest_experimental"
          }}

          env {{
            name  = "GOOGLE_CLOUD_PROJECT"
            value = var.project_id
          }}

          env {{
            name  = "GOOGLE_CLOUD_LOCATION"
            value = "global"
          }}

          env {{
            name  = "GOOGLE_GENAI_USE_VERTEXAI"
            value = "True"
          }}

          resources {{
            requests = {{
              cpu    = "0.5"
              memory = "1Gi"
            }}
            limits = {{
              cpu    = "1"
              memory = "2Gi"
            }}
          }}

          startup_probe {{
            tcp_socket {{
              port = 8080
            }}
            initial_delay_seconds = 10
            period_seconds        = 10
            failure_threshold     = 18
          }}

          readiness_probe {{
            tcp_socket {{
              port = 8080
            }}
            initial_delay_seconds = 15
            period_seconds        = 10
          }}

          liveness_probe {{
            tcp_socket {{
              port = 8080
            }}
            initial_delay_seconds = 15
            period_seconds        = 20
          }}
        }}
      }}
    }}
  }}

  lifecycle {{
    ignore_changes = [
      spec[0].template[0].spec[0].container[0].image,
    ]
  }}

  depends_on = [kubernetes_namespace_v1.app]
}}
''',
        encoding="utf-8",
    )

    (dest / "Dockerfile").write_text(
        '''FROM python:3.12-slim
WORKDIR /code
COPY . .
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
''',
        encoding="utf-8",
    )

    app_utils = dest / "app" / "app_utils"
    app_utils.mkdir(parents=True, exist_ok=True)
    (app_utils / "services.py").write_text(
        '''SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"


def get_session_service():
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    return InMemorySessionService()
''',
        encoding="utf-8",
    )
    (dest / "app" / "fast_api_app.py").write_text(
        '''otel_to_cloud = True
''',
        encoding="utf-8",
    )

    cb_dir = dest / ".cloudbuild"
    cb_dir.mkdir(parents=True, exist_ok=True)
    (cb_dir / "staging.yaml").write_text(
        '''steps:
  - name: "gcr.io/cloud-builders/gcloud"
    id: deploy-staging
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        uvx google-agents-cli deploy --project ${_STAGING_PROJECT_ID} --region ${_REGION}

  - name: "gcr.io/cloud-builders/gcloud"
    id: load_test
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        uvx locust==2.31.1 -f tests/load_test/load_test.py --headless -t 30s -u 10 -r 0.5

substitutions:
  _STAGING_PROJECT_ID: YOUR_STAGING_PROJECT_ID
  _REGION: us-east1
''',
        encoding="utf-8",
    )
    return dest
