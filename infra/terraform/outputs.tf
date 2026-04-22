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

output "training_job_name" {
  description = "Cloud Run Job name for the weekly training pipeline"
  value       = google_cloud_run_v2_job.training.name
}

output "mlflow_vm_ip" {
  description = "External IP of the MLflow VM"
  value       = google_compute_instance.mlflow.network_interface[0].access_config[0].nat_ip
}

output "mlflow_vm_internal_ip" {
  description = "Internal IP of the MLflow VM"
  value       = google_compute_instance.mlflow.network_interface[0].network_ip
}

output "mlflow_url_internal" {
  description = "MLflow tracking URI for use in airflow.env (internal network)"
  value       = "http://${google_compute_instance.mlflow.network_interface[0].network_ip}:5000"
}

output "mlflow_ui_url" {
  description = "MLflow UI URL (public)"
  value       = "http://${google_compute_instance.mlflow.network_interface[0].access_config[0].nat_ip}:5000"
}
