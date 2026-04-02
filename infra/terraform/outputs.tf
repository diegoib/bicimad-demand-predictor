output "gcs_bucket_name" {
  description = "Cloud Storage bucket for raw snapshots"
  value       = google_storage_bucket.raw_data.name
}

output "bq_dataset_id" {
  description = "BigQuery dataset ID"
  value       = google_bigquery_dataset.bicimad.dataset_id
}

output "bq_table_station_status_raw" {
  description = "BigQuery table for raw station snapshots"
  value       = "${var.project_id}.${google_bigquery_dataset.bicimad.dataset_id}.${google_bigquery_table.station_status_raw.table_id}"
}

output "service_account_email" {
  description = "Service account email for ingestion"
  value       = google_service_account.bicimad_ingestion.email
}

output "service_account_key_base64" {
  description = "Base64-encoded service account key JSON — save to gcp-key.json"
  value       = google_service_account_key.bicimad_ingestion_key.private_key
  sensitive   = true
}

output "airflow_vm_ip" {
  description = "External IP of the Airflow VM"
  value       = google_compute_instance.airflow.network_interface[0].access_config[0].nat_ip
}

output "airflow_ui_url" {
  description = "Airflow webserver URL"
  value       = "http://${google_compute_instance.airflow.network_interface[0].access_config[0].nat_ip}:8080"
}
