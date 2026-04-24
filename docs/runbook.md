# Runbook — BiciMAD Demand Predictor

Guía operacional para despliegue, mantenimiento y recuperación de fallos.

---

## Índice

1. [Arquitectura de despliegue](#1-arquitectura-de-despliegue)
2. [Primer despliegue en GCP](#2-primer-despliegue-en-gcp)
   - 2.8 [Levantar MLflow](#28-levantar-mlflow-en-la-vm-bicimad-mlflow)
   - 2.9 [GitHub Actions secrets](#29-configurar-github-actions)
3. [Verificación end-to-end](#3-verificación-end-to-end)
4. [Operaciones habituales](#4-operaciones-habituales)
5. [Recuperación de fallos](#5-recuperación-de-fallos)
6. [Secretos y credenciales](#6-secretos-y-credenciales)

---

## 1. Arquitectura de despliegue

```
GitHub main branch
    │
    ├─ CI (lint + tests en cada PR)
    └─ CD on merge:
         ├─ Build training image → Artifact Registry
         ├─ Update Cloud Run Job bicimad-training
         └─ SSH VM: git pull + restart scheduler

VM e2-medium (bicimad-airflow)
    └─ Docker Compose
         ├─ airflow-webserver  :8080
         ├─ airflow-scheduler
         └─ postgres (metadata DB)

VM e2-medium (bicimad-mlflow)
    └─ Docker Compose (docker-compose.mlflow.yml)
         ├─ mlflow-server  :5000
         └─ postgres (MLflow metadata DB)

DAGs (en ~/bicimad-demand-predictor/dags/):
    ├─ bicimad_ingestion       */15 * * * *   → BQ station_status_raw + predictions
    ├─ bicimad_training        0 3 * * 0      → Cloud Run Job → modelo en GCS + MLflow
    └─ bicimad_daily_monitoring 5 6 * * *     → station_daily_metrics + drift report

GCP:
    ├─ Cloud Storage: bicimad-data-{project}
    │    ├─ raw/station_status/dt=.../hh=.../mm=....json
    │    ├─ models/v{YYYYMMDD_HHMMSS}/model.txt + metadata.json
    │    ├─ monitoring/drift/YYYY-MM-DD.html + _summary.json
    │    └─ mlflow-artifacts/   (artefactos y experimentos del model registry)
    └─ BigQuery: dataset bicimad
         ├─ station_status_raw   (partición ingestion_timestamp)
         ├─ predictions          (partición prediction_made_at)
         ├─ cycle_metrics        (partición cycle_timestamp)
         ├─ station_daily_metrics (partición date)
         └─ daily_totals         (partición date)
```

---

## 2. Primer despliegue en GCP

### 2.1 Requisitos previos

- Cuenta GCP con permisos de Owner o Editor en el proyecto y **billing habilitado** en el proyecto (sin billing no se pueden crear VMs ni usar ciertas APIs)
- `gcloud` CLI instalado y autenticado:
  ```bash
  gcloud auth login                        # autenticación interactiva
  gcloud auth application-default login    # necesario para que Terraform use ADC
  gcloud config set project YOUR_PROJECT_ID
  ```
- `terraform` >= 1.5
- `docker` instalado localmente (para build de la imagen de training)

### 2.2 Terraform

```bash
cd infra/terraform

# Crear workspace y aplicar
terraform init
terraform apply \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="region=europe-west1" \
  -var="alert_email=tu@email.com"

# Guardar outputs
terraform output -json > /tmp/tf_outputs.json
```

Esto crea: VM, GCS bucket, BQ dataset + tablas, service account, Secret Manager secrets.

### 2.3 Secretos EMT

```bash
# Cargar credenciales de la API EMT
echo -n "tu@email.com" | gcloud secrets versions add bicimad-emt-email \
  --data-file=- --project=YOUR_PROJECT_ID

echo -n "tupassword" | gcloud secrets versions add bicimad-emt-password \
  --data-file=- --project=YOUR_PROJECT_ID
```

### 2.4 Descargar clave del service account

```bash
# La clave se crea en Terraform y el output está en base64
terraform output -raw service_account_key_base64 | base64 -d > gcp-key.json
chmod 600 gcp-key.json
```

> **Importante:** verifica que `infra/gcp-key.json` está en `.gitignore` antes de hacer cualquier commit. Este archivo contiene una clave privada y nunca debe subirse al repositorio.

### 2.5 Artifact Registry para la imagen de training

```bash
# Habilitar las APIs necesarias (si no lo hizo Terraform)
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  --project=YOUR_PROJECT_ID

# Crear repositorio (una sola vez)
gcloud artifacts repositories create bicimad \
  --repository-format=docker \
  --location=europe-west1 \
  --project=YOUR_PROJECT_ID

# Build y push inicial de la imagen de training
# --platform linux/amd64 es obligatorio si compilas desde Mac con chip Apple Silicon
gcloud auth configure-docker europe-west1-docker.pkg.dev
docker build --platform linux/amd64 -f infra/training/Dockerfile \
  -t europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/bicimad/training:latest .
docker push europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/bicimad/training:latest
```

### 2.6 Crear Cloud Run Job de training

```bash
gcloud run jobs create bicimad-training \
  --image=europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/bicimad/training:latest \
  --region=europe-west1 \
  --service-account=bicimad-ingestion@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars="BICIMAD_GCS_BUCKET=bicimad-data-YOUR_PROJECT_ID,BICIMAD_GCP_PROJECT=YOUR_PROJECT_ID,BICIMAD_BQ_DATASET=bicimad" \
  --memory=4Gi \
  --cpu=2 \
  --max-retries=1 \
  --task-timeout=30m \
  --project=YOUR_PROJECT_ID
```

### 2.7 Desplegar Airflow en la VM

```bash
# SSH a la VM
gcloud compute ssh bicimad-airflow --zone=europe-west1-b

# 1. Clonar el repo
git clone https://github.com/YOUR_ORG/bicimad-demand-predictor.git ~/bicimad-demand-predictor
cd ~/bicimad-demand-predictor

# 2. Copiar la clave del service account (descargada en §2.4)
# (desde tu máquina local en otra terminal)
gcloud compute scp gcp-key.json bicimad-airflow:~/bicimad-demand-predictor/infra/gcp-key.json \
  --zone=europe-west1-b
chmod 600 ~/bicimad-demand-predictor/infra/gcp-key.json

# 3. Crear airflow.env y rellenar los valores requeridos
cp infra/airflow.env.example infra/airflow.env
# Editar: BICIMAD_GCS_BUCKET, BICIMAD_GCP_PROJECT,
#         AIRFLOW__CORE__FERNET_KEY, AIRFLOW__WEBSERVER__SECRET_KEY
#
# Para generar los valores de las claves:
#   AIRFLOW__CORE__FERNET_KEY:
#     python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   AIRFLOW__WEBSERVER__SECRET_KEY:
#     openssl rand -hex 32
nano infra/airflow.env

# 4. Instalar make si la VM no lo tiene (imágenes Debian mínimas no lo incluyen)
sudo apt-get install -y make

# 5. Inicializar y arrancar Airflow
make airflow-up

# 6. Verificar que los contenedores están healthy
docker compose -f infra/docker-compose.yml ps
# → postgres, airflow-webserver y airflow-scheduler deben aparecer con estado "healthy"

# 5. Configurar Variables de Airflow (una sola vez)
make airflow-vars GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1
```

### 2.8 Levantar MLflow en la VM bicimad-mlflow

```bash
# SSH a la VM de MLflow
gcloud compute ssh bicimad-mlflow --zone=europe-west1-b

# 1. Clonar el repo
git clone https://github.com/YOUR_ORG/bicimad-demand-predictor.git ~/bicimad-demand-predictor
cd ~/bicimad-demand-predictor

# 2. Copiar la clave del service account (misma que la de Airflow)
# (desde tu máquina local en otra terminal)
gcloud compute scp gcp-key.json bicimad-mlflow:~/bicimad-demand-predictor/infra/gcp-key.json \
  --zone=europe-west1-b
chmod 600 ~/bicimad-demand-predictor/infra/gcp-key.json

# 3. Crear mlflow.env y rellenar el bucket
cp infra/mlflow.env.example infra/mlflow.env
# Editar: BICIMAD_GCS_BUCKET=bicimad-data-YOUR_PROJECT_ID
nano infra/mlflow.env

# 4. Arrancar MLflow
make mlflow-up
```

La UI está disponible en `http://MLFLOW_VM_IP:5000`.

Obtener la IP interna de la VM de MLflow y configurarla en `infra/airflow.env` de la VM de Airflow:

```bash
# Obtener IP interna
gcloud compute instances describe bicimad-mlflow \
  --zone=europe-west1-b \
  --format='get(networkInterfaces[0].networkIP)'

# En infra/airflow.env (VM de Airflow), añadir/actualizar:
# BICIMAD_MLFLOW_TRACKING_URI=http://MLFLOW_VM_INTERNAL_IP:5000
```

Tras editar `airflow.env`, reiniciar Airflow: `make airflow-down && make airflow-up`.

### 2.9 Configurar GitHub Actions

En la configuración del repositorio GitHub → Settings → Secrets and Variables → Actions, añadir:

| Secret | Valor |
|--------|-------|
| `GCP_PROJECT_ID` | ID del proyecto GCP |
| `GCP_REGION` | `europe-west1` |
| `GCP_SA_KEY` | Contenido JSON de la service account key (descargada en §2.4) |
| `AIRFLOW_VM_IP` | IP externa de la VM (de `terraform output airflow_vm_ip`) |
| `AIRFLOW_VM_SSH_KEY` | Clave privada SSH para conectar a la VM |
| `AIRFLOW_VM_USER` | Usuario SSH de la VM (`debian` en imágenes Debian de GCP) |

### 2.10 Habilitar DAGs en Airflow

Acceder al UI (`http://VM_IP:8080`, admin/admin) y activar:
- `bicimad_ingestion`
- `bicimad_training`
- `bicimad_daily_monitoring`

> **Seguridad:** cambia la contraseña de `admin` en cuanto entres por primera vez: **Admin → Security → List Users**.

---

## 3. Verificación end-to-end

### 3.1 Verificar ingesta (primer ciclo, ~15 min tras activar el DAG)

Antes de activar el DAG, puedes probar la ingesta manualmente desde la VM de Airflow:

```bash
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "cd ~/bicimad-demand-predictor && PYTHONPATH=. python -m src.ingestion.main"
# → debe mostrar el nº de estaciones descargadas y confirmación de escritura en GCS/BQ
```

```bash
# Ver últimos logs del DAG de ingesta (tras activarlo)
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.yml logs --tail=50 airflow-scheduler"

# Verificar datos en GCS
gsutil ls "gs://bicimad-data-YOUR_PROJECT_ID/raw/station_status/" | tail -5

# Verificar filas en BQ
bq query --nouse_legacy_sql \
  "SELECT COUNT(*), MAX(ingestion_timestamp) FROM \`YOUR_PROJECT_ID.bicimad.station_status_raw\`"
```

### 3.2 Verificar predicciones (tras 2 ciclos, necesita modelo en GCS)

```bash
bq query --nouse_legacy_sql \
  "SELECT COUNT(*), MAX(prediction_made_at) FROM \`YOUR_PROJECT_ID.bicimad.predictions\`"
```

Si el modelo no existe aún, la fase de predicción falla de forma no fatal y el ciclo de ingesta continúa.

### 3.3 Verificar reconciliación (tras ~1h con predicciones)

```bash
bq query --nouse_legacy_sql \
  "SELECT cycle_timestamp, n_predictions, mae, rmse FROM \`YOUR_PROJECT_ID.bicimad.cycle_metrics\`
   ORDER BY cycle_timestamp DESC LIMIT 5"
```

### 3.4 Disparar entrenamiento manual (sin esperar al domingo)

```bash
# Opción 1: trigger desde Airflow UI (botón "Trigger DAG")

# Opción 2: Cloud Run Job directo
gcloud run jobs execute bicimad-training \
  --region=europe-west1 \
  --project=YOUR_PROJECT_ID

# Ver logs del job
gcloud run jobs executions list --job=bicimad-training \
  --region=europe-west1 --project=YOUR_PROJECT_ID
```

### 3.5 Verificar modelo entrenado

```bash
gsutil ls "gs://bicimad-data-YOUR_PROJECT_ID/models/"
# → debe aparecer un directorio v{YYYYMMDD_HHMMSS}/

gsutil cat "gs://bicimad-data-YOUR_PROJECT_ID/models/$(gsutil ls gs://bicimad-data-YOUR_PROJECT_ID/models/ | sort | tail -1)metadata.json" \
  | python -m json.tool | grep '"mae"'
```

El entrenamiento también registra el experimento en MLflow y promueve el modelo al alias `@prod` en el model registry. Verificar en la UI de MLflow (`http://MLFLOW_VM_IP:5000`) → **Models** → `bicimad-forecast`.

> **Nota — feature warmup:** El primer entrenamiento tras un despliegue nuevo requiere al menos 7 días de datos en BigQuery para que las features de lag y rolling estén completas. Lanzar training antes producirá un modelo con métricas degradadas. Este período se controla con `BICIMAD_FEATURE_WARMUP_DAYS` en `airflow.env` (por defecto: 7).

### 3.6 Verificar monitorización diaria

```bash
# Trigger manual del DAG de monitorización desde Airflow UI,
# o ejecutar directamente:
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "cd ~/bicimad-demand-predictor && PYTHONPATH=. python -m src.monitoring.daily_metrics --date $(date -d 'yesterday' +%F)"

# Verificar tablas
bq query --nouse_legacy_sql \
  "SELECT date, n_stations, daily_mae FROM \`YOUR_PROJECT_ID.bicimad.daily_totals\` ORDER BY date DESC LIMIT 3"

# Verificar drift report en GCS
gsutil ls "gs://bicimad-data-YOUR_PROJECT_ID/monitoring/drift/"
```

### 3.7 Verificar alertas

```bash
# Simular degradación: las alertas se loggean como WARNING, no lanzan excepciones
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "cd ~/bicimad-demand-predictor && PYTHONPATH=. python -m src.monitoring.alerts"
# → buscar "PERFORMANCE ALERT" o "DRIFT ALERT" en la salida
```

---

## 4. Operaciones habituales

### Actualizar código (sin CD automático)

```bash
make deploy-vm VM_IP=YOUR_VM_IP VM_KEY=~/.ssh/bicimad_vm
```

### Forzar reentrenamiento

```bash
make run-training-job GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1
```

### Backfill de ingesta (rellenar huecos históricos)

```bash
# Desde el Airflow UI: DAG bicimad_ingestion → Calendar → seleccionar rango → Backfill
# O desde CLI en la VM:
docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.yml exec airflow-webserver \
  airflow dags backfill bicimad_ingestion \
    --start-date 2026-01-01 \
    --end-date 2026-01-02
```

### Ver logs de Airflow

```bash
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.yml logs -f --tail=100 airflow-scheduler"
```

### Reiniciar Airflow completo

```bash
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "cd ~/bicimad-demand-predictor && make airflow-down && make airflow-up"
```

### Consultar la API de predicciones (Serving)

La API de serving es una aplicación FastAPI que lee la tabla `predictions` de BigQuery:

```bash
# Arrancar localmente (requiere Application Default Credentials o gcp-key.json)
make serve
# → http://localhost:8000

# Endpoints:
# GET /health
# GET /predictions/latest          → última predicción de todas las estaciones
# GET /predictions/{station_id}    → predicciones de una estación concreta
```

> La API es actualmente una herramienta de consulta local/dev. No está desplegada como servicio permanente en producción.

### Ver dashboard de monitorización

```bash
# Generar manualmente y obtener URL firmada (válida 1 hora)
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "cd ~/bicimad-demand-predictor && PYTHONPATH=. python -m src.monitoring.dashboard"

gsutil signurl -d 1h gcp-key.json \
  "gs://bicimad-data-YOUR_PROJECT_ID/monitoring/dashboard/index.html"
```

### Retención de datos

El bucket GCS tiene una lifecycle rule de **365 días**: los objetos más antiguos se eliminan automáticamente. Esto limita el expanding window de training a un máximo de ~1 año de histórico. Si se necesitan datos más allá de ese período, exportarlos a BigQuery antes de que expiren o ajustar la regla en Terraform (`lifecycle_rules` en `infra/terraform/main.tf`).

---

## 5. Recuperación de fallos

### DAG de ingesta falla repetidamente

1. Ver logs en Airflow UI → tarea fallida → Log
2. Causas comunes:
   - **Token EMT expirado**: el código renueva automáticamente usando Secret Manager. Si persiste el error 401, borrar el cache del token (`rm /tmp/.bicimad_token_cache.json` en la VM de Airflow dentro del contenedor) para forzar reautenticación inmediata. Si Secret Manager no responde, revisar permisos del service account.
   - **BQ streaming insert timeout**: transitorio, el DAG reintenta 3 veces con backoff
   - **Open-Meteo no disponible**: la ingesta continúa sin datos meteorológicos; el campo `weather_snapshot` quedará `null` en esos registros. Es comportamiento esperado, no indica un bug.
   - **Errores de permisos en GCS/BQ**: comprobar que `GOOGLE_APPLICATION_CREDENTIALS` en `infra/airflow.env` apunta a `/opt/airflow/keys/gcp-key.json` y que el fichero está montado en el contenedor: `docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.yml exec airflow-webserver ls /opt/airflow/keys/`

### DAG de training falla (Cloud Run Job)

```bash
# Ver ejecuciones recientes
gcloud run jobs executions list --job=bicimad-training \
  --region=europe-west1 --project=YOUR_PROJECT_ID

# Ver logs de una ejecución específica
gcloud run jobs executions describe EXECUTION_NAME \
  --region=europe-west1 --project=YOUR_PROJECT_ID

gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="bicimad-training"' \
  --limit=50 --project=YOUR_PROJECT_ID
```

Causas comunes:
- **OOM**: aumentar `--memory` en el job (`gcloud run jobs update bicimad-training --memory=8Gi ...`)
- **Datos insuficientes**: el entrenamiento necesita al menos 7 días de datos en BQ

### VM de Airflow no responde

```bash
# Reiniciar la VM
gcloud compute instances reset bicimad-airflow \
  --zone=europe-west1-b --project=YOUR_PROJECT_ID

# Tras el reinicio, Docker y Docker Compose arrancan solos (configurado en startup script)
# Verificar estado
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.yml ps"
```

### MLflow no responde o el modelo no tiene alias @prod

```bash
# Verificar estado del servidor MLflow
gcloud compute ssh bicimad-mlflow --zone=europe-west1-b -- \
  "docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.mlflow.yml ps"

# Ver logs
gcloud compute ssh bicimad-mlflow --zone=europe-west1-b -- \
  "docker compose -f ~/bicimad-demand-predictor/infra/docker-compose.mlflow.yml logs --tail=50 mlflow"
```

Si el Cloud Run Job de training termina sin error pero el alias `@prod` no aparece en el model registry:
- Revisar logs del job buscando `MLflowException` o errores de conexión
- Verificar que `BICIMAD_MLFLOW_TRACKING_URI` en `infra/airflow.env` apunta a la IP interna correcta de la VM de MLflow

Para promover manualmente una versión al alias `@prod` desde la UI:
1. Abrir `http://MLFLOW_VM_IP:5000` → **Models** → `bicimad-forecast`
2. Seleccionar la versión deseada → **Aliases** → añadir `prod`

O desde la VM de Airflow vía Python:

```bash
gcloud compute ssh bicimad-airflow --zone=europe-west1-b -- \
  "cd ~/bicimad-demand-predictor && PYTHONPATH=. python - <<'EOF'
import mlflow, os
client = mlflow.MlflowClient(os.environ['BICIMAD_MLFLOW_TRACKING_URI'])
client.set_registered_model_alias('bicimad-forecast', 'prod', 'VERSION_NUMBER')
EOF"
```

### Tablas BQ con datos incorrectos (rollback)

Las tablas usan streaming insert (no transaccional). Para corregir:

```bash
# Borrar filas de un rango temporal (requiere rol bigquery.dataEditor)
bq query --nouse_legacy_sql \
  "DELETE FROM \`YOUR_PROJECT_ID.bicimad.cycle_metrics\`
   WHERE DATE(cycle_timestamp) = '2026-01-15'"
```

---

## 6. Secretos y credenciales

| Secreto | Dónde vive | Rotación |
|---------|------------|----------|
| Token EMT | Secret Manager (`bicimad-emt-email`, `bicimad-emt-password`) | Manual cuando caduca el contrato EMT |
| Service account key | `/infra/gcp-key.json` en VM + GitHub Secret `GCP_SERVICE_ACCOUNT` | Anual (o usar Workload Identity) |
| Fernet key Airflow | `infra/airflow.env` en VM | No rotar sin migrar la BD de metadatos |
| SSH key de la VM | GitHub Secret `AIRFLOW_VM_SSH_KEY` | Anual |

### Rotar la clave del service account

```bash
# Crear nueva clave
gcloud iam service-accounts keys create /tmp/new-key.json \
  --iam-account=bicimad-ingestion@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Copiar a la VM
gcloud compute scp /tmp/new-key.json \
  bicimad-airflow:~/bicimad-demand-predictor/infra/gcp-key.json --zone=europe-west1-b

# Actualizar GitHub Secret AIRFLOW_VM_SSH_KEY con el nuevo valor
# (desde la UI de GitHub o con gh CLI):
gh secret set GCP_SA_KEY < /tmp/new-key.json

# Borrar clave antigua (listar primero)
gcloud iam service-accounts keys list \
  --iam-account=bicimad-ingestion@YOUR_PROJECT_ID.iam.gserviceaccount.com
gcloud iam service-accounts keys delete OLD_KEY_ID \
  --iam-account=bicimad-ingestion@YOUR_PROJECT_ID.iam.gserviceaccount.com
```
