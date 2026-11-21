# VPC Network
resource "google_compute_network" "gke_network" {
  name                    = "${var.project_name}-network"
  project                 = var.project_id
  auto_create_subnetworks = false

  depends_on = [resource.google_project_service.services]
}

# Subnet for GKE cluster
resource "google_compute_subnetwork" "gke_subnet" {
  name          = "${var.project_name}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.gke_network.id
  ip_cidr_range = "10.0.0.0/20"
}

# GKE Autopilot Cluster
resource "google_container_cluster" "app" {
  name     = var.project_name
  location = var.region
  project  = var.project_id

  network    = google_compute_network.gke_network.name
  subnetwork = google_compute_subnetwork.gke_subnet.name

  enable_autopilot = true

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  ip_allocation_policy {}

  deletion_protection = false

  depends_on = [
    resource.google_project_service.services,
  ]
}

# Cloud Router & NAT for private GKE nodes
resource "google_compute_router" "router" {
  name    = "${var.project_name}-router"
  project = var.project_id
  region  = var.region
  network = google_compute_network.gke_network.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.project_name}-nat"
  project                            = var.project_id
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Artifact Registry for container images
resource "google_artifact_registry_repository" "docker_repo" {
  location      = var.region
  repository_id = var.project_name
  format        = "DOCKER"
  project       = var.project_id

  depends_on = [resource.google_project_service.services]
}

# Workload Identity binding (KSA -> GSA)
resource "google_service_account_iam_member" "workload_identity_binding" {
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.project_name}/${var.project_name}]"

  depends_on = [google_container_cluster.app]
}

# --- Kubernetes Resources ---

resource "kubernetes_namespace_v1" "app" {
  metadata {
    name = var.project_name
  }
  depends_on = [google_container_cluster.app]
}

resource "kubernetes_service_account_v1" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.app_sa.email
    }
  }
}

resource "kubernetes_service_v1" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      app = var.project_name
    }
    annotations = {
      "cloud.google.com/load-balancer-type" = "Internal"
    }
  }
  spec {
    type = "LoadBalancer"
    port {
      port        = 8080
      target_port = 8080
      protocol    = "TCP"
    }
    selector = {
      app = var.project_name
    }
  }
}

resource "kubernetes_horizontal_pod_autoscaler_v2" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      app = var.project_name
    }
  }
  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = var.project_name
    }
    min_replicas = 2
    max_replicas = 10
    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 70
        }
      }
    }
  }
}

resource "kubernetes_pod_disruption_budget_v1" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      app = var.project_name
    }
  }
  spec {
    min_available = 1
    selector {
      match_labels = {
        app = var.project_name
      }
    }
  }
}

resource "kubernetes_deployment_v1" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      app = var.project_name
    }
  }

  spec {
    selector {
      match_labels = {
        app = var.project_name
      }
    }

    template {
      metadata {
        labels = {
          app = var.project_name
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.app.metadata[0].name

        container {
          name  = var.project_name
          image = "us-docker.pkg.dev/cloudrun/container/hello"

          port {
            container_port = 8080
            protocol       = "TCP"
          }

          env {
            name  = "LOGS_BUCKET_NAME"
            value = google_storage_bucket.logs_data_bucket.name
          }

          env {
            name  = "OTEL_SERVICE_NAME"
            value = "gke-enterprise-agent"
          }

          env {
            name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
            value = "NO_CONTENT"
          }

          env {
            name  = "OTEL_SEMCONV_STABILITY_OPT_IN"
            value = "gen_ai_latest_experimental"
          }

          env {
            name  = "GOOGLE_CLOUD_PROJECT"
            value = var.project_id
          }

          env {
            name  = "GOOGLE_CLOUD_LOCATION"
            value = "global"
          }

          env {
            name  = "GOOGLE_GENAI_USE_VERTEXAI"
            value = "True"
          }

          resources {
            requests = {
              cpu    = "0.5"
              memory = "1Gi"
            }
            limits = {
              cpu    = "1"
              memory = "2Gi"
            }
          }

          startup_probe {
            tcp_socket {
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 10
            failure_threshold     = 18
          }

          readiness_probe {
            tcp_socket {
              port = 8080
            }
            initial_delay_seconds = 15
            period_seconds        = 10
          }

          liveness_probe {
            tcp_socket {
              port = 8080
            }
            initial_delay_seconds = 15
            period_seconds        = 20
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      spec[0].template[0].spec[0].container[0].image,
    ]
  }

  depends_on = [kubernetes_namespace_v1.app]
}
