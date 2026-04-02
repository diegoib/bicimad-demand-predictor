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

Con Airflow en marcha, hay que verificar que la ingesta funciona primero en local y luego activar el DAG para que se ejecute automáticamente cada 15 minutos.

---

### Paso 10: Probar la ingesta en local

Antes de activar la ingesta en producción, verifica que el código funciona en tu máquina.

**10.1 — Crear el fichero `.env` con tus credenciales de EMT:**

En la raíz del repositorio crea un fichero `.env` (está en `.gitignore`, no se subirá a Git):
```bash
BICIMAD_ENV=dev
BICIMAD_EMT_EMAIL=tu_email@ejemplo.com
BICIMAD_EMT_PASSWORD=tu_contraseña
```

Las credenciales son las del portal de desarrolladores de EMT Madrid ([mobilitylabs.emtmadrid.es](https://mobilitylabs.emtmadrid.es)). Si aún no tienes cuenta, regístrate ahí primero.

**10.2 — Instalar las dependencias del proyecto:**
```bash
make setup
```

**10.3 — Ejecutar una ingesta de prueba (con datos mock, sin llamar a la API real):**
```bash
make ingest-test
```
Esto verifica que el código compila y que la escritura de ficheros funciona, sin gastar llamadas a la API.

**10.4 — Ejecutar una ingesta real:**
```bash
make ingest-local
```

Si todo va bien, verás en la terminal el número de estaciones descargadas y los datos meteorológicos. Se habrán creado ficheros JSON en `data/raw/` con esta estructura:
```
data/raw/station_status/dt=YYYY-MM-DD/hh=HH/mm=MM.json
```

**10.5 — Verificar el contenido:**
```bash
ls data/raw/station_status/
# Navega hasta el fichero más reciente y ábrelo para confirmar que tiene datos válidos
```

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

> *Esta sección se completará próximamente.*

---

## Parte 4: Despliegue de la API de predicción

> *Esta sección se completará próximamente.*

---

## Referencia rápida: comandos más usados

```bash
# Levantar Airflow local (desarrollo)
make airflow-up

# Ejecutar una ingesta manualmente
make ingest-local

# Ejecutar los tests
make test

# Lint
make lint

# Destruir la infraestructura en GCP (¡cuidado!)
cd infra/terraform && terraform destroy -var="project_id=TU_PROJECT_ID"
```
