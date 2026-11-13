# deployment/terraform/budget.tf
# [C6.1] Optional Cloud Billing Budget & Pub/Sub Alert (50% / 80% / 100%)
# Enabled automatically when `var.billing_account_id` is provided.

resource "google_project_service" "billing_budgets_api" {
  project            = var.project_id
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

variable "billing_account_id" {
  type        = string
  description = "Google Cloud Billing Account ID (optional; enables budget resource when set)"
  default     = ""
}

variable "monthly_budget_amount_usd" {
  type        = number
  description = "Monthly spend cap in USD for the agent project"
  default     = 1000
}

resource "google_pubsub_topic" "budget_circuit_breaker" {
  project    = var.project_id
  name       = "${var.project_name}-budget-alerts"
  depends_on = [google_project_service.billing_budgets_api]
}

resource "google_billing_budget" "agent_monthly_budget" {
  count           = var.billing_account_id != "" ? 1 : 0
  billing_account = var.billing_account_id
  display_name    = "${var.project_name}-monthly-spend-cap"

  budget_filter {
    projects = ["projects/${data.google_project.project.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_amount_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.8
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    pubsub_topic = google_pubsub_topic.budget_circuit_breaker.id
  }

  depends_on = [google_project_service.billing_budgets_api]
}
