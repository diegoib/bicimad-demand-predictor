terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  bucket_name = var.gcs_bucket_name != "" ? var.gcs_bucket_name : "bicimad-data-${var.project_id}"
}

# ---------------------------------------------------------------------------
# APIs
# ---------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Cloud Storage — raw snapshots
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "raw_data" {
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition { age = 365 }
    action { type = "Delete" }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# BigQuery — processed data
# ---------------------------------------------------------------------------

resource "google_bigquery_dataset" "bicimad" {
  dataset_id  = var.bq_dataset_id
  location    = var.region
  description = "BiciMAD demand predictor — ingested station snapshots"

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_table" "station_status_raw" {
  dataset_id          = google_bigquery_dataset.bicimad.dataset_id
  table_id            = "station_status_raw"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "ingestion_timestamp"
  }

  clustering = ["id"]

  schema = jsonencode([
    { name = "id",           type = "INTEGER",   mode = "REQUIRED",  description = "Station internal ID (EMT)" },
    { name = "number",       type = "STRING",    mode = "NULLABLE",  description = "Human-readable station number" },
    { name = "name",         type = "STRING",    mode = "NULLABLE",  description = "Station name" },
    { name = "activate",     type = "INTEGER",   mode = "NULLABLE",  description = "1 = active, 0 = inactive" },
    { name = "no_available", type = "INTEGER",   mode = "NULLABLE",  description = "1 = not available, 0 = available" },
    { name = "total_bases",  type = "INTEGER",   mode = "NULLABLE",  description = "Total docking slots" },
    { name = "dock_bikes",   type = "INTEGER",   mode = "NULLABLE",  description = "Bikes currently docked (available to rent)" },
    { name = "free_bases",   type = "INTEGER",   mode = "NULLABLE",  description = "Free docking slots (available to return)" },
    {
      name = "geometry", type = "RECORD", mode = "NULLABLE",
      fields = [
        { name = "type",        type = "STRING",  mode = "NULLABLE" },
        { name = "coordinates", type = "FLOAT64", mode = "REPEATED", description = "[longitude, latitude]" }
      ]
    },
    { name = "ingestion_timestamp", type = "TIMESTAMP", mode = "REQUIRED", description = "UTC timestamp of the ingestion cycle" },
    {
      name = "weather_snapshot", type = "RECORD", mode = "NULLABLE",
      fields = [
        { name = "timestamp",                 type = "TIMESTAMP", mode = "NULLABLE" },
        { name = "temperature_2m",            type = "FLOAT64",   mode = "NULLABLE", description = "Air temperature at 2m, °C" },
        { name = "apparent_temperature",      type = "FLOAT64",   mode = "NULLABLE", description = "Feels-like temperature, °C" },
        { name = "precipitation",             type = "FLOAT64",   mode = "NULLABLE", description = "Precipitation mm" },
        { name = "precipitation_probability", type = "FLOAT64",   mode = "NULLABLE", description = "Probability of precipitation, %" },
        { name = "wind_speed_10m",            type = "FLOAT64",   mode = "NULLABLE", description = "Wind speed at 10m, km/h" },
        { name = "weather_code",              type = "INTEGER",   mode = "NULLABLE", description = "WMO weather interpretation code" },
        { name = "is_day",                    type = "INTEGER",   mode = "NULLABLE", description = "1 = daytime, 0 = nighttime" },
        { name = "direct_radiation",          type = "FLOAT64",   mode = "NULLABLE", description = "Direct solar radiation, W/m²" }
      ]
    }
  ])
}

# ---------------------------------------------------------------------------
# BigQuery — batch predictions
# ---------------------------------------------------------------------------

resource "google_bigquery_table" "predictions" {
  dataset_id          = google_bigquery_dataset.bicimad.dataset_id
  table_id            = "predictions"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "prediction_made_at"
  }

  clustering = ["station_id"]

  schema = jsonencode([
    { name = "station_id",           type = "INTEGER",   mode = "REQUIRED", description = "Station internal ID (EMT)" },
    { name = "prediction_made_at",   type = "TIMESTAMP", mode = "REQUIRED", description = "UTC snapshot timestamp when prediction was made (t)" },
    { name = "target_time",          type = "TIMESTAMP", mode = "REQUIRED", description = "UTC time the prediction applies to (t+1h)" },
    { name = "predicted_dock_bikes", type = "FLOAT64",   mode = "REQUIRED", description = "Predicted number of docked bikes at target_time" },
    { name = "model_version",        type = "STRING",    mode = "REQUIRED", description = "Model version string (e.g. v20260115_100000)" }
  ])
}

resource "google_bigquery_table" "cycle_metrics" {
  dataset_id          = google_bigquery_dataset.bicimad.dataset_id
  table_id            = "cycle_metrics"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "cycle_timestamp"
  }

  clustering = ["model_version"]

  schema = jsonencode([
    { name = "cycle_timestamp",    type = "TIMESTAMP", mode = "REQUIRED", description = "Snapshot timestamp that was reconciled (T-1h)" },
    { name = "model_version",      type = "STRING",    mode = "REQUIRED", description = "Model version string" },
    { name = "n_predictions",      type = "INTEGER",   mode = "REQUIRED", description = "Number of stations reconciled in this cycle" },
    { name = "mae",                type = "FLOAT64",   mode = "REQUIRED", description = "Mean absolute error across all reconciled stations" },
    { name = "rmse",               type = "FLOAT64",   mode = "REQUIRED", description = "Root mean squared error" },
    { name = "p50_error",          type = "FLOAT64",   mode = "REQUIRED", description = "Median absolute error (50th percentile)" },
    { name = "p90_error",          type = "FLOAT64",   mode = "REQUIRED", description = "90th percentile absolute error" },
    { name = "worst_station_id",   type = "INTEGER",   mode = "REQUIRED", description = "Station ID with the highest absolute error" },
    { name = "worst_station_error",type = "FLOAT64",   mode = "REQUIRED", description = "Absolute error of the worst station" },
    { name = "reconciled_at",      type = "TIMESTAMP", mode = "REQUIRED", description = "UTC timestamp when reconciliation ran" }
  ])
}

resource "google_bigquery_table" "daily_totals" {
  dataset_id          = google_bigquery_dataset.bicimad.dataset_id
  table_id            = "daily_totals"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "date"
  }

  clustering = ["model_version"]

  schema = jsonencode([
    { name = "date",          type = "DATE",    mode = "REQUIRED", description = "Calendar date (UTC)" },
    { name = "model_version", type = "STRING",  mode = "REQUIRED", description = "Model version used for predictions on this date" },
    { name = "n_stations",    type = "INTEGER", mode = "REQUIRED", description = "Number of distinct stations with predictions on this date" },
    { name = "n_cycles",      type = "INTEGER", mode = "REQUIRED", description = "Total (station, cycle) pairs reconciled on this date" },
    { name = "daily_mae",     type = "FLOAT64", mode = "REQUIRED", description = "Overall mean absolute error across all stations and cycles" },
    { name = "daily_rmse",    type = "FLOAT64", mode = "REQUIRED", description = "Overall root mean squared error" }
  ])
}

resource "google_bigquery_table" "station_daily_metrics" {
  dataset_id          = google_bigquery_dataset.bicimad.dataset_id
  table_id            = "station_daily_metrics"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "date"
  }

  clustering = ["station_id"]

  schema = jsonencode([
    { name = "date",          type = "DATE",    mode = "REQUIRED", description = "Calendar date (UTC)" },
    { name = "station_id",    type = "INTEGER", mode = "REQUIRED", description = "Station internal ID (EMT)" },
    { name = "model_version", type = "STRING",  mode = "REQUIRED", description = "Model version used for predictions on this date" },
    { name = "n_cycles",      type = "INTEGER", mode = "REQUIRED", description = "Number of cycles reconciled for this station on this date" },
    { name = "daily_mae",     type = "FLOAT64", mode = "REQUIRED", description = "Mean absolute error across all cycles for this station and date" },
    { name = "daily_rmse",    type = "FLOAT64", mode = "REQUIRED", description = "Root mean squared error across all cycles" }
  ])
}

# ---------------------------------------------------------------------------
# Service Account — ingestion & Airflow
# ---------------------------------------------------------------------------

resource "google_service_account" "bicimad_ingestion" {
  account_id   = "bicimad-ingestion"
  display_name = "BiciMAD Ingestion Service Account"
  description  = "Used by Airflow and Cloud Function to ingest and store data"
}

# GCS: write raw snapshots
resource "google_storage_bucket_iam_member" "ingestion_gcs_write" {
  bucket = google_storage_bucket.raw_data.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.bicimad_ingestion.email}"
}

# GCS: read (for training, serving model loading)
resource "google_storage_bucket_iam_member" "ingestion_gcs_read" {
  bucket = google_storage_bucket.raw_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.bicimad_ingestion.email}"
}

# BigQuery: insert rows
resource "google_bigquery_dataset_iam_member" "ingestion_bq_editor" {
  dataset_id = google_bigquery_dataset.bicimad.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.bicimad_ingestion.email}"
}

resource "google_project_iam_member" "ingestion_bq_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.bicimad_ingestion.email}"
}

# Secret Manager: read EMT credentials
resource "google_project_iam_member" "ingestion_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.bicimad_ingestion.email}"
}

# Cloud Run: trigger training job from Airflow (developer includes runWithOverrides)
resource "google_project_iam_member" "ingestion_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.bicimad_ingestion.email}"
}

# Service account key — download once and store securely
resource "google_service_account_key" "bicimad_ingestion_key" {
  service_account_id = google_service_account.bicimad_ingestion.name
}

# ---------------------------------------------------------------------------
# Secret Manager — EMT credentials
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "emt_email" {
  secret_id = "bicimad-emt-email"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "emt_password" {
  secret_id = "bicimad-emt-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# NOTE: Secret values must be set manually after apply:
#   echo -n "your@email.com" | gcloud secrets versions add bicimad-emt-email --data-file=-
#   echo -n "yourpassword"   | gcloud secrets versions add bicimad-emt-password --data-file=-

# ---------------------------------------------------------------------------
# Airflow VM — e2-medium
# ---------------------------------------------------------------------------

resource "google_compute_instance" "airflow" {
  name         = "bicimad-airflow"
  machine_type = "e2-medium"
  zone         = "${var.region}-b"

  tags = ["airflow", "http-server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20  # GB
    }
  }

  network_interface {
    network = "default"
    access_config {}  # ephemeral public IP
  }

  service_account {
    email  = google_service_account.bicimad_ingestion.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -e
    # Wait for apt lock released by unattended-upgrades on first boot
    systemd-run --property="After=apt-daily.service apt-daily-upgrade.service" \
      --wait /bin/true 2>/dev/null || true
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do sleep 2; done
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg git make
    # Docker official repo (docker-compose-plugin not in Debian default repos)
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    usermod -aG docker $(logname 2>/dev/null || echo debian)
  EOT

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Cloud Run Job — weekly model training
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "training" {
  name     = "bicimad-training"
  location = var.region

  template {
    template {
      service_account = google_service_account.bicimad_ingestion.email
      max_retries     = 3
      timeout         = "1800s"

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/bicimad/training:latest"

        resources {
          limits = {
            cpu    = "1000m"
            memory = "2Gi"
          }
        }

        env {
          name  = "BICIMAD_GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "BICIMAD_BQ_DATASET"
          value = var.bq_dataset_id
        }
        env {
          name  = "BICIMAD_GCS_BUCKET"
          value = local.bucket_name
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Firewall rule — Airflow webserver (port 8080)
resource "google_compute_firewall" "airflow_webserver" {
  name    = "allow-airflow-webserver"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  source_ranges = ["0.0.0.0/0"]  # Restrict to your IP in production
  target_tags   = ["airflow"]
}
