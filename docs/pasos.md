# Guía de puesta en marcha — BiciMAD Demand Predictor

Esta guía recorre todos los pasos necesarios para poner en marcha el sistema completo, desde cero. Está pensada para seguirse en orden.

---

## Parte 1: Infraestructura en GCP

### Paso 1: Crear una cuenta de Google Cloud

Si aún no tienes una cuenta de GCP:

1. Ve a [cloud.google.com](https://cloud.google.com) y haz clic en **Get started for free**.
2. Necesitarás una cuenta de Google (Gmail sirve) y una tarjeta de crédito para verificación. Google ofrece 300 $ de crédito gratuito al registrarte — no se cobra nada automáticamente al agotarse.

---

### Paso 2: Instalar las herramientas necesarias en tu máquina

Necesitas dos herramientas en tu terminal:

**Google Cloud CLI (`gcloud`):**
Descárgala desde [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install) y sigue las instrucciones para tu sistema operativo. Cuando termine, ejecuta:
```bash
gcloud init
```
Esto te pedirá que inicies sesión con tu cuenta de Google y configures el proyecto por defecto.

**Terraform:**
Descárgalo desde [developer.hashicorp.com/terraform/install](https://developer.hashicorp.com/terraform/install). Verifica que está instalado con:
```bash
terraform -version
```

---

### Paso 3: Crear un proyecto en GCP

Un proyecto es el contenedor de todos los recursos (bases de datos, máquinas virtuales, etc.). Crea uno desde la terminal:

```bash
gcloud projects create mi-proyecto-bicimad
gcloud config set project mi-proyecto-bicimad
```

O desde la consola web en [console.cloud.google.com](https://console.cloud.google.com) → menú superior → **New Project**.

> Anota el **Project ID** — lo necesitarás en el siguiente paso. No es lo mismo que el nombre; el ID es único a nivel global (p. ej. `bicimad-prod-2024`).

---

### Paso 4: Activar la facturación (billing)

Sin billing activado, GCP no permite crear máquinas virtuales ni usar algunas APIs.

1. Ve a [console.cloud.google.com/billing](https://console.cloud.google.com/billing).
2. Crea una cuenta de facturación y vincúlala al proyecto que acabas de crear.

Recuerda que los primeros 300 $ son gratuitos si es una cuenta nueva.

---

### Paso 5: Autenticarte desde el terminal

Terraform necesita credenciales para crear recursos en tu nombre. Ejecuta:

```bash
gcloud auth application-default login
```

Se abrirá el navegador para que inicies sesión con tu cuenta de Google. Una vez hecho, Terraform usará esas credenciales automáticamente.

---

### Paso 6: Desplegar la infraestructura con Terraform

Desde la raíz del repositorio:

```bash
cd infra/terraform
terraform init
terraform apply -var="project_id=TU_PROJECT_ID"
```

**¿Qué hace cada comando?**

- `terraform init` — Descarga el plugin de Google Cloud para Terraform. Solo hace falta ejecutarlo una vez (o cuando se cambia la versión del plugin).
- `terraform apply` — Crea todos los recursos en GCP: bucket de almacenamiento, base de datos en BigQuery, máquina virtual para Airflow, service account con sus permisos, y los contenedores para las credenciales en Secret Manager.

Terraform te mostrará un resumen de lo que va a crear y te pedirá confirmación. Escribe `yes` y pulsa Enter.

El proceso tarda unos 2-3 minutos. Al terminar verás los valores de salida: IP de la VM, nombre del bucket, etc.

---

### Paso 7: Subir las credenciales de la API de BiciMAD

Terraform crea los "cajones" donde guardar las credenciales, pero no puede conocer tus contraseñas. Hay que subirlas manualmente:

```bash
echo -n "tu_email@ejemplo.com" | gcloud secrets versions add bicimad-emt-email --data-file=-
echo -n "tu_contraseña"        | gcloud secrets versions add bicimad-emt-password --data-file=-
```

Estas son las credenciales con las que te registraste en el portal de desarrolladores de EMT Madrid ([mobilitylabs.emtmadrid.es](https://mobilitylabs.emtmadrid.es)).

---

### Paso 8: Obtener la clave de la service account

La service account es la "identidad" que usa Airflow para escribir datos en GCS y BigQuery. Necesitas descargar su clave:

```bash
terraform output -raw service_account_key_base64 | base64 -d > ../../infra/gcp-key.json
```

Esto crea el archivo `infra/gcp-key.json`. **Importante:**
- Este archivo contiene una clave privada — nunca lo subas a Git.
- Verifica que `infra/gcp-key.json` está en el `.gitignore` antes de hacer cualquier commit.

---

### Paso 9: Levantar Airflow en la VM

Con la VM creada por Terraform, hay que copiar el código, configurar las credenciales y arrancar los contenedores.

**9.1 — Obtener la IP de la VM:**
```bash
cd infra/terraform
terraform output airflow_vm_ip
```
Guarda esa IP — la usarás para SSH y para acceder a la UI.

**9.2 — Copiar la clave de la service account a la VM:**
```bash
gsutil cp infra/gcp-key.json gs://TU_BUCKET/tmp/gcp-key.json
```

**9.3 — Conectarte por SSH:**
```bash
gcloud compute ssh TU_USUARIO@bicimad-airflow --zone=europe-west1-b
```
Sustituye `TU_USUARIO` por el nombre de usuario de tu cuenta de Google (la parte antes de `@gmail.com`). Puedes consultarlo con `gcloud auth list`.

Los siguientes comandos se ejecutan **dentro de la VM**.

**9.4 — Clonar el repositorio:**
```bash
git clone https://github.com/TU_USUARIO/TU_REPO.git bicimad
cd bicimad
```

**9.5 — Descargar la clave de la service account desde GCS:**
```bash
gsutil cp gs://TU_BUCKET/tmp/gcp-key.json infra/gcp-key.json
gsutil rm gs://TU_BUCKET/tmp/gcp-key.json
```
El segundo comando borra la clave del bucket — no debe quedar ahí permanentemente.

**9.6 — Crear y rellenar el fichero `infra/airflow.env`:**
```bash
cp infra/airflow.env.example infra/airflow.env
```

Edita `infra/airflow.env` con un editor de texto (`nano infra/airflow.env`) y rellena los campos obligatorios:

| Variable | Cómo obtenerla |
|---|---|
| `AIRFLOW__CORE__FERNET_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `AIRFLOW__WEBSERVER__SECRET_KEY` | `openssl rand -hex 32` |
| `BICIMAD_GCS_BUCKET` | El nombre del bucket (sale en `terraform output`) |
| `BICIMAD_BQ_PROJECT` | Tu Project ID de GCP |

Las variables de SMTP son opcionales — puedes dejarlas vacías si no quieres alertas por email.

**9.7 — Instalar `make` y arrancar Airflow:**
```bash
sudo apt-get install -y make
make airflow-up
```

Este comando ejecuta `docker compose up -d` desde `infra/`. La primera vez tarda 3-5 minutos porque descarga las imágenes y las dependencias Python adicionales.

**9.8 — Verificar que todo está en marcha:**
```bash
docker compose -f infra/docker-compose.yml ps
```
Deberías ver `postgres`, `airflow-webserver` y `airflow-scheduler` con estado `healthy`.

**9.9 — Acceder a la UI de Airflow:**

Abre en el navegador: `http://IP_DE_LA_VM:8080`

Usuario: `admin` | Contraseña: `admin`

> Cambia la contraseña de `admin` en cuanto entres: **Admin → Security → List Users**.

---

## Parte 2: Ingesta de datos

Con Airflow en marcha, hay que verificar que la ingesta funciona antes de activar el DAG para que se ejecute automáticamente cada 15 minutos.

---

### Paso 10: Probar la ingesta manualmente

Antes de activar el DAG, verifica que el código funciona lanzando una ejecución manual.

**10.1 — Configura ADC contra el proyecto de desarrollo:**

```bash
gcloud auth application-default login
export BICIMAD_BQ_PROJECT=bicimad-dev  # o el proyecto que uses
```

Las credenciales de la API de EMT se leen desde Google Secret Manager (secretos `bicimad-emt-email` y `bicimad-emt-password`). Asegúrate de que existen en tu proyecto GCP.

**10.2 — Instalar las dependencias del proyecto:**
```bash
make setup
```

**10.3 — Ejecutar los tests para verificar que el código está en buen estado:**
```bash
make test
```

**10.4 — Lanzar una ejecución manual del DAG de ingesta** desde la UI de Airflow o directamente:
```bash
python -m src.ingestion.main
```

Si todo va bien, verás en la terminal el número de estaciones descargadas y los datos meteorológicos. Los datos se habrán escrito en GCS y BigQuery.

---

### Paso 11: Activar el DAG de ingesta en Airflow

Una vez verificado que la ingesta funciona localmente, actívala en la UI de Airflow para que se ejecute cada 15 minutos en producción.

**11.1 — Abre la UI de Airflow** en `http://IP_DE_LA_VM:8080` y busca el DAG `bicimad_ingestion`.

**11.2 — Activa el DAG** haciendo clic en el toggle de la izquierda (pasará de gris a azul).

**11.3 — Lanza una ejecución manual** para verificar que funciona antes de esperar al siguiente ciclo de 15 minutos:
- Haz clic en el DAG → botón **Trigger DAG** (icono ▶).

**11.4 — Revisa los logs de la ejecución:**
- Haz clic en el círculo verde (o rojo si falla) → **Log**.
- Deberías ver las líneas de log indicando cuántas estaciones se procesaron y si los datos se escribieron en GCS.

---

### Paso 12: Verificar los datos en GCS y BigQuery

**En Google Cloud Storage:**
```bash
gsutil ls gs://TU_BUCKET/station_status/
gsutil ls gs://TU_BUCKET/station_status/dt=$(date +%Y-%m-%d)/
```

**En BigQuery** (desde la consola web o con `bq`):
```bash
bq query --use_legacy_sql=false \
  "SELECT COUNT(*) as rows, MIN(ingestion_timestamp) as first, MAX(ingestion_timestamp) as last
   FROM \`TU_PROJECT.bicimad.station_status_raw\`"
```

Deberías ver filas con `ingestion_timestamp` correspondiente a las últimas ejecuciones.

> Si el DAG falla con errores de permisos, comprueba que `GOOGLE_APPLICATION_CREDENTIALS` en `airflow.env` apunta a `/opt/airflow/keys/gcp-key.json` y que el fichero está montado correctamente (`docker compose -f infra/docker-compose.yml exec airflow-webserver ls /opt/airflow/keys/`).

---

## Parte 3: Entrenamiento del modelo

El modelo se entrena en un **Cloud Run Job** llamado `bicimad-training`. Esto lo mantiene separado de la VM de Airflow (que tiene RAM limitada) y permite ejecutarlo bajo demanda o de forma automática cada semana. El DAG `bicimad_training` lo dispara automáticamente los domingos a las 03:00 UTC.

---

### Paso 13: Habilitar APIs y crear el repositorio de imágenes

Cloud Run y Artifact Registry no vienen habilitados por defecto. Actívalos y crea el repositorio donde se subirá la imagen Docker del entrenador:

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project=TU_PROJECT_ID

gcloud artifacts repositories create bicimad \
  --repository-format=docker \
  --location=TU_REGION \
  --project=TU_PROJECT_ID
```

Sustituye `TU_REGION` por la región que uses (p. ej. `europe-west1`).

---

### Paso 14: Construir y subir la imagen de entrenamiento

Desde la raíz del repositorio en tu máquina local:

```bash
# Autenticarse en Artifact Registry
gcloud auth configure-docker TU_REGION-docker.pkg.dev

# Construir y subir
docker build -f infra/training/Dockerfile \
  -t TU_REGION-docker.pkg.dev/TU_PROJECT_ID/bicimad/training:latest \
  .
docker push TU_REGION-docker.pkg.dev/TU_PROJECT_ID/bicimad/training:latest
```

> A partir del primer push a `main`, el pipeline de CI/CD (GitHub Actions) se encargará de reconstruir y actualizar la imagen automáticamente.

---

### Paso 15: Crear el Cloud Run Job

Este comando crea el Job en GCP. Solo hace falta ejecutarlo una vez:

```bash
gcloud run jobs create bicimad-training \
  --image TU_REGION-docker.pkg.dev/TU_PROJECT_ID/bicimad/training:latest \
  --region TU_REGION \
  --project TU_PROJECT_ID \
  --service-account bicimad-ingestion@TU_PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars "BICIMAD_GCS_BUCKET=TU_BUCKET,BICIMAD_BQ_PROJECT=TU_PROJECT_ID,BICIMAD_BQ_DATASET=bicimad" \
  --memory 2Gi \
  --task-timeout 30m
```

La cuenta de servicio `bicimad-ingestion` es la misma que usa la VM — ya tiene los permisos necesarios sobre GCS y BigQuery (la creó Terraform en el paso 6).

---

### Paso 16: Configurar las variables de Airflow

El DAG de entrenamiento necesita saber en qué proyecto y región está el Cloud Run Job. Configúralo ejecutando esto **en la VM**, dentro del directorio del proyecto:

```bash
make airflow-vars GCP_PROJECT=TU_PROJECT_ID GCP_REGION=TU_REGION
```

Esto establece las variables `bicimad_gcp_project` y `bicimad_gcp_region` en Airflow (se guardan en la base de datos de Airflow y persisten entre reinicios).

---

### Paso 17: Lanzar un entrenamiento manual para verificar

Antes de activar el DAG, comprueba que el Job funciona lanzándolo a mano:

```bash
make run-training-job GCP_PROJECT=TU_PROJECT_ID GCP_REGION=TU_REGION
```

Puedes seguir los logs en tiempo real en la consola de GCP: **Cloud Run → Jobs → bicimad-training → Executions**.

El Job tarda unos 5-10 minutos la primera vez (descarga datos de BigQuery, entrena LightGBM, sube el modelo a GCS). Cuando termine, verifica que el modelo se guardó correctamente:

```bash
gsutil ls gs://TU_BUCKET/models/
```

Deberías ver un directorio con el formato `v20260101_030000/` que contiene `model.txt` y `metadata.json`.

---

### Paso 18: Activar los DAGs de entrenamiento y monitorización

En la UI de Airflow (`http://IP_DE_LA_VM:8080`), activa los dos DAGs restantes:

| DAG | Schedule | Qué hace |
|---|---|---|
| `bicimad_training` | Domingos 03:00 UTC | Reentrena el modelo semanalmente con los últimos 28 días de datos |
| `bicimad_daily_monitoring` | Diariamente 06:05 UTC | Calcula métricas de error del día anterior, genera reporte de drift y lanza alertas si algo va mal |

Para activar cada uno: haz clic en el toggle de la izquierda (pasará de gris a azul).

---

## Parte 4: API de predicción

La API FastAPI en `src/serving/app.py` expone las predicciones que genera el pipeline de ingesta. Lee directamente de la tabla `predictions` en BigQuery — no carga el modelo en memoria, solo sirve resultados ya calculados.

---

### Paso 19: Levantar la API en modo desarrollo

Requiere tener ADC configurado y al menos un ciclo de ingesta completado (para que haya predicciones en BigQuery):

```bash
gcloud auth application-default login
export BICIMAD_BQ_PROJECT=TU_PROJECT_ID

make serve
# API disponible en http://localhost:8000
# Documentación interactiva en http://localhost:8000/docs
```

---

### Paso 20: Verificar los endpoints

```bash
# Liveness check
curl http://localhost:8000/health

# Predicciones de todas las estaciones (última snapshot disponible)
curl http://localhost:8000/predictions/latest | python3 -m json.tool | head -40

# Predicción de una estación concreta (p. ej. estación 1)
curl http://localhost:8000/predictions/1
```

La respuesta de `/predictions/latest` es una lista de objetos con esta estructura:

```json
{
  "station_id": 1,
  "prediction_made_at": "2026-04-13T06:00:00+00:00",
  "target_time": "2026-04-13T07:00:00+00:00",
  "predicted_dock_bikes": 12.4,
  "model_version": "v20260413_030000"
}
```

`prediction_made_at` es el momento en que se tomó el snapshot; `target_time` es t+1h, el momento para el que se predice.

> **Nota:** El despliegue de la API en Cloud Run con acceso público está previsto para la siguiente fase del proyecto. Por ahora, `make serve` es suficiente para verificar que el sistema completo funciona de extremo a extremo.

---

## Referencia rápida: comandos más usados

```bash
# Levantar / parar Airflow
make airflow-up
make airflow-down

# Configurar variables de Airflow (una vez tras airflow-up, en la VM)
make airflow-vars GCP_PROJECT=TU_PROJECT_ID GCP_REGION=TU_REGION

# Ejecutar una ingesta manualmente
python -m src.ingestion.main

# Lanzar el entrenamiento manualmente (Cloud Run Job)
make run-training-job GCP_PROJECT=TU_PROJECT_ID GCP_REGION=TU_REGION

# Levantar la API de predicciones (modo desarrollo)
make serve

# Ejecutar los tests
make test

# Lint
make lint

# Destruir la infraestructura en GCP (¡cuidado!)
cd infra/terraform && terraform destroy -var="project_id=TU_PROJECT_ID"
```
