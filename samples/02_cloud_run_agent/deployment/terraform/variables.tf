variable "project_name" {
  type    = string
  default = "cr-enterprise-agent"
}

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east1"
}

variable "app_sa_roles" {
  type = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/secretmanager.secretAccessor",
  ]
}
