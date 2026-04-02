variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "europe-west1"
}

variable "gcs_bucket_name" {
  description = "Name for the Cloud Storage bucket (must be globally unique)"
  type        = string
  default     = ""  # defaults to bicimad-data-{project_id} in main.tf
}

variable "bq_dataset_id" {
  description = "BigQuery dataset ID"
  type        = string
  default     = "bicimad"
}

variable "alert_email" {
  description = "Email for Airflow failure alerts"
  type        = string
  default     = ""
}
