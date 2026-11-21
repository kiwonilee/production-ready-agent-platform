data "google_project" "project" {
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
