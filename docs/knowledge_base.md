# Índice

1. [Infraestructura GCP con Terraform](#1-infraestructura-gcp-con-terraform)
2. [Apache Airflow en este proyecto](#2-apache-airflow-en-este-proyecto)
3. [MLflow en este proyecto](#3-mlflow-en-este-proyecto)
4. [Logging](#4-logging)
5. [Pre-commit](#5-pre-commit)
6. [Flujo de entrenamiento e inferencia](#6-flujo-de-entrenamiento-e-inferencia)
7. [Ingesta](#7-ingesta)
8. [Monitorización](#8-monitorización)
9. [Servidor MCP (Model Context Protocol)](#9-servidor-mcp-model-context-protocol)
10. [Arquitecturas de despliegue de un servidor MCP](#10-arquitecturas-de-despliegue-de-un-servidor-mcp)
11. [stdout y stderr en el transporte stdio de MCP](#11-stdout-y-stderr-en-el-transporte-stdio-de-mcp)
12. [Manejo de errores: de errors.py a ToolError](#12-manejo-de-errores-de-errorspy-a-toolerror)
13. [Qué es un REPL y por qué se ha construido aquí](#13-qué-es-un-repl-y-por-qué-se-ha-construido-aquí)

---

# 1. Infraestructura GCP con Terraform

## Los tres archivos de Terraform

Terraform divide la configuración en tres archivos por convención:

### `variables.tf` — Parámetros de entrada

Define las variables que puedes pasar al ejecutar `terraform apply`. Solo `project_id` es obligatorio (no tiene `default`). Las demás tienen valores por defecto sensatos:

- `region` → `europe-west1` (Bélgica)
- `gcs_bucket_name` → vacío, lo que hace que `main.tf` lo calcule como `bicimad-data-{project_id}`
- `bq_dataset_id` → `bicimad`

### `main.tf` — Recursos a crear

Es el grueso de la configuración. Crea estos recursos en orden (respetando dependencias):

| Bloque | Qué crea |
|---|---|
| `google_project_service` | Activa 6 APIs de GCP (Storage, BigQuery, Secret Manager, Cloud Functions, Cloud Scheduler, Compute) |
| `google_storage_bucket` | Bucket GCS con borrado automático de objetos a los 365 días |
| `google_bigquery_dataset` + `google_bigquery_table` | Dataset `bicimad` y tabla `station_status_raw` con schema explícito, partición diaria por `ingestion_timestamp` y clustering por `id` |
| `google_service_account` + IAM members | Service account `bicimad-ingestion` con permisos mínimos: escribir/leer GCS, insertar en BigQuery, leer secrets |
| `google_service_account_key` | Genera una clave JSON para autenticarse como esa service account |
| `google_secret_manager_secret` (x2) | Crea los "contenedores" para los secrets `bicimad-emt-email` y `bicimad-emt-password` (sin valor todavía — hay que subirlos manualmente después) |
| `google_compute_instance` | VM e2-medium con Debian 12, 20 GB disco, IP pública efímera, y un startup script que instala Docker |
| `google_compute_firewall` | Regla que abre el puerto 8080 (Airflow UI) desde cualquier IP |

### `outputs.tf` — Valores de salida

Tras el `apply`, Terraform imprime estos valores para que los puedas usar:

- Nombre del bucket, dataset y tabla de BigQuery
- Email de la service account
- La clave de la service account en Base64 (marcada como `sensitive`, no se imprime automáticamente)
- IP pública y URL de la VM de Airflow

---

## Despliegue paso a paso

### 1. Inicializar y aplicar

```bash
cd infra/terraform && terraform init && terraform apply -var="project_id=TU_PROJECT"
```

Se ejecuta en tres pasos encadenados:

**`cd infra/terraform`** — Entra al directorio donde están los `.tf`. Terraform siempre trabaja sobre el directorio actual.

**`terraform init`** — Inicialización única (o cuando cambian los providers). Descarga el provider de Google (`hashicorp/google ~> 5.0`) de la registry de Terraform y crea la carpeta `.terraform/`. Sin esto, `apply` falla.

**`terraform apply -var="project_id=TU_PROJECT"`** — El comando principal. Hace:
1. Lee los tres archivos `.tf` y construye un grafo de dependencias.
2. Llama a la API de GCP para ver qué ya existe (estado actual).
3. Calcula el diff (qué crear/modificar/destruir) y te muestra el plan.
4. Te pide confirmación (`yes`).
5. Crea todos los recursos en el orden correcto según dependencias.

El resultado se guarda en `terraform.tfstate` (no lo toques manualmente — es la fuente de verdad de Terraform sobre lo que existe).

### 2. Subir las credenciales EMT a Secret Manager

```bash
echo -n "tu_email@emtmadrid.es" | gcloud secrets versions add bicimad-emt-email --data-file=-
echo -n "tu_contraseña"         | gcloud secrets versions add bicimad-emt-password --data-file=-
```

Terraform crea los contenedores de los secrets pero no puede subir los valores (son credenciales externas). Hay que añadirlos manualmente con estos comandos.

### 3. Obtener la clave de la service account

```bash
terraform output -raw service_account_key_base64 | base64 -d > infra/gcp-key.json
```

Este comando tiene tres partes encadenadas:

**`terraform output -raw service_account_key_base64`** — Lee el output del estado de Terraform. Sin `-raw` saldría entre comillas; con `-raw` sale el string limpio. Como está marcado `sensitive = true`, solo se puede leer así explícitamente (no aparece en el `apply` normal).

**`| base64 -d`** — GCP siempre devuelve las claves de service account codificadas en Base64. Esto lo decodifica a JSON plano.

**`> infra/gcp-key.json`** — Guarda el JSON resultante. Es la clave privada que Airflow usará para autenticarse como la service account y poder escribir en GCS y BigQuery.

> **Importante:** `infra/gcp-key.json` contiene una clave privada. Debe estar en `.gitignore` y nunca commitearse al repositorio.

---

## Variables opcionales

Para sobreescribir cualquier valor por defecto:

```bash
terraform apply \
  -var="project_id=mi-proyecto" \
  -var="region=europe-southwest1" \
  -var="gcs_bucket_name=mi-bucket-unico" \
  -var="bq_dataset_id=bicimad_prod"
```

---

## Destruir la infraestructura

```bash
terraform destroy -var="project_id=TU_PROJECT"
```

Elimina todos los recursos creados. Pide confirmación antes de ejecutar. El bucket tiene `force_destroy = false`, por lo que fallará si contiene datos — hay que vaciarlo primero.

[↑ Volver al índice](#índice)

---

# 2. Apache Airflow en este proyecto

## Qué es Airflow y para qué sirve aquí

Apache Airflow es un **orquestador de pipelines**: no ejecuta lógica de negocio, sino que decide *cuándo* y *en qué orden* ejecutar código que vive en otro sitio. En este proyecto, todo el código real está en `src/`; los DAGs son la capa fina que lo invoca según un calendario.

En este proyecto Airflow se encarga de dos pipelines:
- **Ingesta** (`bicimad_ingestion`): cada 15 minutos, llama a `src/ingestion/main.py` para capturar el estado de las 634 estaciones de BiciMAD y los datos meteorológicos y escribirlos en GCS y BigQuery.
- **Entrenamiento** (`bicimad_training`): diariamente a las 03:00 UTC, lanza un Cloud Run Job que construye el dataset de features, entrena el modelo LightGBM y lo registra en GCS y MLflow.
- **Monitorización** (`bicimad_daily_monitoring`): diariamente a las 06:05 UTC, agrega métricas de error, genera el drift report y ejecuta las alertas.

---

## Componentes necesarios para ejecutar Airflow

Airflow no es un proceso único — son varios procesos que trabajan juntos:

### Base de datos de metadatos (PostgreSQL)
Es la pieza central. Almacena el estado de todo: qué DAGs existen, qué runs han ocurrido, qué tareas están en cola, en ejecución o fallidas, logs de auditoría, usuarios, conexiones y variables. Sin ella Airflow no arranca.

En este proyecto corre en un contenedor Docker (`postgres:15-alpine`) en la misma VM. En producción sería una base de datos gestionada externa.

### Scheduler
El cerebro de Airflow. Se encarga de:
1. Parsear periódicamente los archivos Python de `dags/` (cada ~30 segundos por defecto)
2. Calcular qué tareas deben ejecutarse según el schedule y las dependencias entre ellas
3. Actualizar el estado de cada tarea en la base de datos
4. Delegar la ejecución al Executor

El scheduler **no ejecuta las tareas directamente** — eso lo hace el Executor.

### Webserver
Sirve la interfaz web en el puerto 8080. Permite ver el estado de los DAGs, ver logs de ejecución, lanzar triggers manuales y gestionar usuarios. No ejecuta ninguna tarea — es solo una ventana a la base de datos de metadatos.

### Executor
Define *cómo* se ejecutan las tareas. En este proyecto se usa `LocalExecutor`, que lanza cada tarea como un subproceso en la misma máquina donde corre el scheduler. Es el modo más sencillo y suficiente para un volumen bajo de tareas.

Se configura con la variable de entorno en `infra/airflow.env`:
```
AIRFLOW__CORE__EXECUTOR=LocalExecutor
```
Esta variable se inyecta en todos los contenedores mediante `env_file: - airflow.env` en el `docker-compose.yml`. Airflow la detecta al arrancar y no requiere ningún archivo de configuración adicional.

Alternativas usadas en entornos más grandes:
- **CeleryExecutor**: distribuye tareas a un pool de workers independientes (máquinas separadas). Requiere Redis o RabbitMQ como message broker.
- **KubernetesExecutor**: crea un pod de Kubernetes por cada tarea. Aislamiento total, escalado automático.

En este proyecto no hay workers separados — el scheduler y los workers son el mismo proceso.

---

## Cómo funciona un DAG

Un DAG (Directed Acyclic Graph) es un archivo Python que define:
- Qué tareas hay y en qué orden deben ejecutarse (grafo de dependencias)
- Con qué frecuencia se ejecuta (`schedule`)
- Parámetros de reintento, alertas y otras opciones (`default_args`)

### Ciclo de vida de una ejecución

1. El scheduler parsea `dags/ingestion_dag.py` y detecta que el schedule `*/15 * * * *` se ha cumplido
2. Crea un **DAG Run** en la base de datos con estado `queued`
3. El Executor lanza la tarea `ingest_stations_and_weather` como subproceso
4. El proceso ejecuta el comando bash, que corre `python -m src.ingestion.main`
5. Si el proceso termina con código 0, la tarea pasa a `success`. Si falla, reintenta (hasta 3 veces con backoff exponencial)

### Logical Date vs Start Date

Este concepto confunde al principio. En Airflow:
- **Logical Date**: el inicio del *intervalo de datos* que representa ese run. Por ejemplo, `06:45`.
- **Start Date** (ejecución real): cuándo arranca el proceso. Con un schedule de 15 minutos, el run de `06:45` empieza a las `07:00` — cuando ese intervalo ha terminado.

Siempre habrá una diferencia de exactamente un intervalo entre ambas columnas. Es el comportamiento correcto y esperado.

### Parámetros clave del DAG de ingesta

```python
with DAG(
    dag_id="bicimad_ingestion",
    schedule="*/15 * * * *",   # cada 15 minutos
    start_date=datetime(2025, 1, 1, tzinfo=UTC),
    catchup=False,             # no recuperar runs perdidos del pasado lejano
    max_active_runs=1,         # si una ejecución tarda más de 15 min, no solapar
    ...
)
```

`catchup=False` evita que Airflow intente ejecutar todos los intervalos desde `start_date` hasta hoy al activar el DAG. Sin él, al activar el DAG intentaría ejecutar miles de runs atrasados.

### Por qué BashOperator y no PythonOperator

Airflow ofrece `PythonOperator` para llamar a funciones Python directamente. El problema en VMs con poca RAM es que usa `fork()` para crear el proceso de la tarea, heredando toda la memoria del scheduler (~2GB en este caso). El kernel acaba matando el proceso por falta de memoria (OOM killer, `exit code -9`).

`BashOperator` lanza un proceso `bash` completamente nuevo, que a su vez lanza `python`. El proceso hijo arranca desde cero con ~100-200MB, sin heredar el estado del scheduler.

```python
ingest_task = BashOperator(
    task_id="ingest_stations_and_weather",
    bash_command="cd /opt/airflow/project && PYTHONPATH=/opt/airflow/project python -m src.ingestion.main",
)
```

---

## El docker-compose.yml explicado

El archivo `infra/docker-compose.yml` define los cuatro servicios necesarios para Airflow más un quinto de inicialización.

### Anchor `x-airflow-common`

```yaml
x-airflow-common: &airflow-common
  build:
    context: .
    dockerfile: Dockerfile.airflow
  env_file:
    - airflow.env
  environment:
    PYTHONPATH: /opt/airflow/project
  volumes:
    - ../dags:/opt/airflow/dags
    - ../src:/opt/airflow/project/src
    ...
```

El bloque `x-airflow-common` es un **anchor YAML** — una forma de definir configuración compartida una sola vez y reutilizarla en múltiples servicios con `<<: *airflow-common`. Evita repetir los mismos volúmenes, variables de entorno e imagen en cada servicio.

### Imagen custom (`Dockerfile.airflow`)

En lugar de usar `image: apache/airflow:2.9.3` directamente, se construye una imagen custom:

```dockerfile
FROM apache/airflow:2.9.3
USER airflow
RUN pip install --no-cache-dir \
    pydantic>=2.0 \
    google-cloud-storage>=2.10 \
    ...
```

El motivo es el rendimiento en VMs pequeñas. La imagen base de Airflow permite instalar dependencias extra en tiempo de ejecución mediante la variable `_PIP_ADDITIONAL_REQUIREMENTS`. Pero esto significa que cada vez que arranca un contenedor (webserver, scheduler) pip instala varios paquetes pesados, consumiendo RAM y CPU durante el arranque — lo que en una VM de 4GB con todo corriendo a la vez causaba OOM.

Con una imagen personalizada, las dependencias están instaladas en la capa Docker y los contenedores arrancan en segundos.

### `airflow-init`

```yaml
airflow-init:
  <<: *airflow-common
  command: bash -c "airflow db migrate && airflow users create --username admin ..."
  restart: "no"
```

Es un contenedor **one-shot**: corre una vez, ejecuta las migraciones de la base de datos y crea el usuario admin, y termina. `restart: "no"` asegura que Docker no lo vuelve a lanzar.

El webserver y el scheduler tienen:
```yaml
depends_on:
  airflow-init:
    condition: service_completed_successfully
```
Esto garantiza que no arrancan hasta que `airflow-init` haya terminado correctamente. Sin esto, el webserver intentaría conectarse a una base de datos vacía y fallaría.

### Servicios principales

| Servicio | Comando | Puerto | Healthcheck |
|---|---|---|---|
| `postgres` | (imagen oficial) | 5432 (interno) | `pg_isready` |
| `airflow-webserver` | `airflow webserver` | 8080 → 8080 | `curl /health` |
| `airflow-scheduler` | `airflow scheduler` | ninguno | `airflow jobs check` |

### Volúmenes nombrados

```yaml
volumes:
  postgres-data:    # datos de PostgreSQL — persisten entre reinicios
  airflow-logs:     # logs de las tareas
  airflow-plugins:  # plugins de Airflow (no usados actualmente)
```

Los volúmenes nombrados persisten aunque se haga `docker compose down`. Solo se eliminan con `docker compose down -v`.

---

## Cómo los contenedores saben dónde están los DAGs

Esta es la parte más importante para entender el flujo de desarrollo.

### Bind mounts vs volúmenes nombrados

El anchor `x-airflow-common` monta varios directorios del host dentro de los contenedores:

```yaml
volumes:
  - ../dags:/opt/airflow/dags              # DAGs del proyecto
  - ../src:/opt/airflow/project/src        # código de negocio
  - ../pyproject.toml:/opt/airflow/project/pyproject.toml:ro
  - airflow-logs:/opt/airflow/logs         # volumen nombrado
  - airflow-plugins:/opt/airflow/plugins   # volumen nombrado
  - ./gcp-key.json:/opt/airflow/keys/gcp-key.json:ro
```

Los primeros tres son **bind mounts**: montan una carpeta real del sistema de archivos del host dentro del contenedor. Son bidireccionales y en tiempo real — si editas `dags/ingestion_dag.py` en tu máquina, el scheduler ve el cambio en los próximos 30 segundos sin reiniciar nada.

### Por qué Airflow sabe dónde buscar los DAGs

Airflow busca DAGs en `/opt/airflow/dags` por defecto (configurable con `AIRFLOW__CORE__DAGS_FOLDER`). El bind mount `../dags:/opt/airflow/dags` hace que esa ruta dentro del contenedor apunte al directorio `dags/` del repositorio en el host.

### Cómo las tareas importan código de `src/`

El comando de la tarea es:
```bash
cd /opt/airflow/project && PYTHONPATH=/opt/airflow/project python -m src.ingestion.main
```

La variable `PYTHONPATH=/opt/airflow/project` le dice a Python dónde buscar módulos. Como `../src` está montado en `/opt/airflow/project/src`, la importación `from src.ingestion.main import ingest` funciona.

Esto significa que el código de `src/` se ejecuta directamente desde el bind mount — cualquier cambio en el código es visible inmediatamente en la siguiente ejecución del DAG, sin reconstruir la imagen Docker.

### La clave GCP

```yaml
- ./gcp-key.json:/opt/airflow/keys/gcp-key.json:ro
```

El archivo `infra/gcp-key.json` (la clave de la service account descargada con `terraform output`) se monta como solo lectura dentro del contenedor. La variable de entorno `GOOGLE_APPLICATION_CREDENTIALS=/opt/airflow/keys/gcp-key.json` en `airflow.env` apunta a esa ruta, de modo que cualquier librería de Google Cloud (`google-cloud-storage`, `google-cloud-bigquery`) la usa automáticamente para autenticarse.

---

## Este proyecto vs producción empresarial

| Aspecto | Este proyecto | Empresa mediana-grande |
|---|---|---|
| **Despliegue** | Self-hosted en VM e2-medium con Docker Compose | Cloud Composer (GCP), MWAA (AWS), Astronomer, o Kubernetes propio |
| **Executor** | LocalExecutor — tareas en serie, misma VM | CeleryExecutor o KubernetesExecutor — tareas en paralelo, workers separados |
| **Base de datos** | PostgreSQL en Docker, misma VM | PostgreSQL gestionado (Cloud SQL, RDS) con réplicas y backups automáticos |
| **Alta disponibilidad** | No — si la VM cae, Airflow cae | Múltiples schedulers activos/pasivos, webservers detrás de load balancer |
| **Escalado** | Manual (redimensionar la VM) | Automático — K8s crea un pod por tarea y lo destruye al terminar |
| **Distribución de DAGs** | Bind mount desde el mismo servidor | git-sync sidecar (sincroniza desde Git automáticamente) o bucket GCS/S3 |
| **Secretos** | Variables en `airflow.env` en el servidor | Secrets Backend integrado (GCP Secret Manager, HashiCorp Vault) |
| **CI/CD** | 3 workflows: lint+tests en PR, build imagen de training, deploy DAGs a VM vía SSH | Deploy automático de DAGs en cada merge a main |
| **Monitorización** | Logs en la UI de Airflow | Integración con Datadog, PagerDuty, alertas en Slack |
| **Coste** | ~13 $/mes (VM e2-medium) | 300–2000 $/mes según número de workers y escala |

La diferencia más importante es el **Executor**. Con LocalExecutor, si el scheduler cae las tareas dejan de ejecutarse y hay que reiniciarlo manualmente. Con KubernetesExecutor, cada tarea es un pod independiente con su propio ciclo de vida — el fallo de un pod no afecta a los demás ni al scheduler.

---

## Variables de entorno (`airflow.env`)

El archivo `infra/airflow.env` (nunca commiteado) configura todos los servicios de Airflow. Airflow usa una convención de nombres `AIRFLOW__SECCION__VARIABLE` para cualquier parámetro de configuración.

| Variable | Para qué sirve |
|---|---|
| `AIRFLOW__CORE__EXECUTOR` | Qué executor usar. `LocalExecutor` = subprocesos en la misma máquina |
| `AIRFLOW__CORE__FERNET_KEY` | Clave simétrica (Fernet) para cifrar valores sensibles almacenados en la BD (contraseñas de conexiones, variables marcadas como secretas). Si cambia, los valores cifrados anteriores no se pueden descifrar |
| `AIRFLOW__WEBSERVER__SECRET_KEY` | Clave para firmar las sesiones web (Flask). Si cambia, todos los usuarios pierden la sesión activa |
| `AIRFLOW__WEBSERVER__EXPOSE_CONFIG` | Si `true`, la UI muestra la configuración completa de Airflow. En `false` para no exponer información sensible |
| `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` | Cadena de conexión a PostgreSQL. El hostname `postgres` resuelve al contenedor del mismo nombre dentro de la red de Docker Compose |
| `BICIMAD_GCS_BUCKET` | Nombre del bucket donde se escriben los snapshots raw |
| `BICIMAD_GCP_PROJECT` | Project ID de GCP para las queries de BigQuery |
| `GOOGLE_APPLICATION_CREDENTIALS` | Ruta a la clave JSON de la service account dentro del contenedor. Las librerías de Google Cloud la detectan automáticamente |

[↑ Volver al índice](#índice)

---

# 3. MLflow en este proyecto

## Qué es MLflow y qué problema resuelve

MLflow es una plataforma open source para gestionar el ciclo de vida de modelos de machine learning. En este proyecto cumple dos funciones separadas que MLflow llama *Tracking* y *Model Registry*.

**Sin MLflow**, cada entrenamiento sobreescribiría el modelo anterior. No habría forma de saber qué conjunto de hiperparámetros produjo qué resultado, ni de volver a una versión anterior si el nuevo modelo empeora. El modelo "en producción" sería simplemente el último archivo `model.txt` en GCS, sin ningún contrato formal sobre qué significa "en producción".

**Con MLflow**, cada entrenamiento crea un *run* inmutable con sus métricas, parámetros y artefactos. El Model Registry permite declarar explícitamente cuál de esos runs es el modelo en producción mediante un *alias*, sin mover ni copiar archivos.

---

## Los dos roles de MLflow

### Tracking — el historial de experimentos

El **Tracking Server** es una base de datos de experimentos. Cada vez que se entrena un modelo se abre un *run* (una ejecución) que registra:

- **Parámetros** (`mlflow.log_params`): valores fijos del entrenamiento — número de features, número de árboles, mejor iteración de LightGBM.
- **Métricas** (`mlflow.log_metrics`): números que cuantifican el resultado — MAE, RMSE del conjunto de test.
- **Artefactos** (`mlflow.log_artifact`, `mlflow.lightgbm.log_model`): archivos asociados — el propio modelo LightGBM, `metadata.json`, los gráficos de feature importance.

Cada run tiene un **`run_id`**: un UUID que identifica ese entrenamiento de forma permanente (ejemplo: `a3f7c2d1e4b9...`). Es la clave primaria de todo lo que pasó en esa ejecución. Se usa para recuperar métricas históricas, para comparar runs entre sí en la UI, y para localizar un modelo concreto en el Registry.

Los runs se agrupan en **experimentos**. En este proyecto existe un único experimento llamado `bicimad-demand-forecast` (configurable con `BICIMAD_MLFLOW_EXPERIMENT`). La UI de MLflow muestra todos los runs del experimento en una tabla comparable.

### Model Registry — el contrato de producción

El **Model Registry** es un catálogo de versiones de modelos. Es independiente del Tracking Server aunque comparte la misma base de datos.

Cuando un run se registra en el Registry (con `mlflow.lightgbm.log_model(..., registered_model_name="bicimad-forecast")`), MLflow crea una **versión** numerada (1, 2, 3…) vinculada a ese `run_id`. La versión hereda todos los artefactos y métricas del run.

Lo que hace al Registry útil son los **aliases**: etiquetas nombradas que apuntan a una versión concreta. En este proyecto hay un alias llamado `prod`. En cada momento, `@prod` apunta a exactamente una versión — la que está en producción. El código que carga el modelo en inferencia nunca dice "carga la versión 7", sino "carga la versión con alias `@prod`":

```python
model_uri = "models:/bicimad-forecast@prod"
booster = mlflow.lightgbm.load_model(model_uri)
```

Si mañana el modelo v8 es mejor, se reasigna el alias `@prod` a v8. El código de inferencia no cambia. Las versiones anteriores siguen existiendo en el Registry — no se borran.

---

## Cómo está desplegado MLflow en este proyecto

MLflow corre en una **VM separada** (`bicimad-mlflow`, e2-medium) con su propio `docker-compose.mlflow.yml`. Usar una VM separada evita competir por RAM con Airflow, que ya consume ~2 GB en su VM.

Dos componentes corren en esa VM:

- **`mlflow` (servidor)**: expone la API REST y la UI web en el puerto 5000. Recibe las llamadas de `log_params`, `log_metrics`, `log_model`, etc.
- **`postgres`**: base de datos donde MLflow persiste todos los metadatos de runs, experimentos, versiones y aliases. Es exclusiva de MLflow (no compartida con la base de datos de Airflow).

Los **artefactos** (el archivo `model.txt`, `metadata.json`, los PNGs) no se guardan en Postgres sino en **GCS**, en la carpeta `mlflow-artifacts/` del bucket. Postgres solo guarda la referencia (la URI `gs://...`) y los metadatos estructurados. Esto es lo que se llama un *artifact store* remoto.

```
Airflow VM                   MLflow VM                 GCS
──────────────────────        ──────────────────────    ──────────────────
register_and_promote()  ───→  mlflow server :5000  ───→ mlflow-artifacts/
                               └─ postgres (metadata)
```

La Airflow VM accede al servidor de MLflow usando la IP interna de la VM de MLflow (`BICIMAD_MLFLOW_TRACKING_URI=http://IP_INTERNA:5000`). Se usa la IP interna (no la pública) porque ambas VMs están en la misma red VPC de GCP — la comunicación interna es más rápida y no consume ancho de banda externo.

---

## El flujo completo de un entrenamiento

El DAG `bicimad_training` tiene dos tareas encadenadas:

### Tarea 1: `run_training_job` (Cloud Run Job)

El Cloud Run Job ejecuta el pipeline de training (construir dataset desde BigQuery, split temporal, entrenar LightGBM con Optuna, evaluar). Al terminar, llama a `save_model()` en `src/training/registry.py`, que:

1. Guarda `model.txt`, `metadata.json`, los archivos de feature importance y `test_set.parquet` (el split de test serializado) en un directorio local temporal.
2. Sube todos esos archivos a GCS en `models/v{YYYYMMDD_HHMMSS}/`.

El Cloud Run Job **no toca MLflow**. Solo escribe en GCS. Esto es deliberado: el job puede estar en cualquier región y no necesita alcanzar la VM de MLflow.

### Tarea 2: `register_and_promote` (PythonOperator en la VM de Airflow)

Esta tarea corre en la VM de Airflow, que sí puede llegar a MLflow por red interna. Llama a `register_and_promote()` en `src/training/registry.py`, que hace tres cosas:

**a) Descarga el modelo de GCS y lo registra en MLflow** (`register_model_to_mlflow`):
- Abre un run en el experimento `bicimad-demand-forecast`.
- Loguea parámetros (número de árboles, features…) y métricas (MAE, RMSE…).
- Llama a `mlflow.lightgbm.log_model(...)` con `registered_model_name="bicimad-forecast"`. Esto hace dos cosas a la vez: sube el modelo como artefacto del run *y* crea una nueva versión en el Model Registry.
- Sube `metadata.json` y los archivos de feature importance como artefactos adicionales del run.
- Devuelve el `run_id` y el MAE del nuevo modelo.

**b) Consulta el modelo actualmente en `@prod`** (`get_prod_model_metrics`):
- Llama a `client.get_model_version_by_alias("bicimad-forecast", "prod")`.
- Recupera el `run_id` de esa versión y busca su MAE en el Tracking Server.
- Si no hay ningún alias `@prod` todavía (primer entrenamiento), devuelve `None`.

**c) Decide si promover** (`promote_to_prod` dentro de `register_and_promote`):
- Si no hay `@prod` → promueve siempre (bootstrap).
- Si hay `@prod`: descarga `test_set.parquet` del modelo nuevo desde GCS, carga el modelo prod desde MLflow y lo evalúa sobre ese mismo test set. Compara los dos MAEs sobre los **mismos datos** — si el nuevo MAE es menor, promueve. Si no mejora, mantiene el alias `@prod` en la versión anterior.
- Fallback: si `test_set.parquet` no existe en GCS (modelos entrenados antes de este cambio), compara con el MAE almacenado en MLflow con un warning en los logs.

La lógica de promoción usa MAE como único criterio. El alias `@prod` se reasigna con:

```python
client.set_registered_model_alias("bicimad-forecast", "prod", version_number)
```

donde `version_number` es el número de versión en el Registry (no el `run_id`). Para encontrar ese número a partir del `run_id`, el código busca en las versiones registradas con `client.search_model_versions(f"run_id='{run_id}'")`).

---

## Cómo se carga el modelo en inferencia

La inferencia ocurre en el DAG de ingesta: tras escribir el snapshot de estaciones en BigQuery, llama a `load_prod_model()`, que:

1. Pide al Registry la versión con alias `@prod` mediante `client.get_model_version_by_alias("bicimad-forecast", "prod")`.
2. Carga el modelo usando el URI `models:/bicimad-forecast@prod` con `mlflow.lightgbm.load_model(...)`. MLflow resuelve ese URI a la URI de GCS donde está el artefacto y lo descarga.
3. Descarga `metadata.json` del run asociado para obtener los nombres de features y métricas.
4. Si no existe ningún alias `@prod` (sistema recién desplegado, aún no hay ningún entrenamiento registrado), hace fallback a `load_latest_model()`, que descarga el modelo más reciente directamente de GCS por nombre de versión.

---

## Variables de configuración

Todas configurables en `infra/airflow.env` con el prefijo `BICIMAD_`:

| Variable | Por defecto | Para qué sirve |
|---|---|---|
| `BICIMAD_MLFLOW_TRACKING_URI` | `http://mlflow:5000` | URI del servidor de MLflow. En producción, la IP interna de la VM de MLflow |
| `BICIMAD_MLFLOW_MODEL_NAME` | `bicimad-forecast` | Nombre del modelo en el Registry. Cambiarlo crea un modelo separado |
| `BICIMAD_MLFLOW_PROD_ALIAS` | `prod` | Nombre del alias que identifica el modelo en producción |
| `BICIMAD_MLFLOW_EXPERIMENT` | `bicimad-demand-forecast` | Nombre del experimento donde se agrupan los runs |

---

## Referencia rápida de conceptos

| Concepto | Qué es | Ejemplo |
|---|---|---|
| **Experiment** | Agrupación de runs relacionados | `bicimad-demand-forecast` |
| **Run** | Una ejecución de entrenamiento con sus métricas y artefactos | run del 2026-04-22 a las 03:00 |
| **`run_id`** | UUID único que identifica un run | `a3f7c2d1e4b9f083...` |
| **Registered Model** | Nombre bajo el que se agrupan las versiones en el Registry | `bicimad-forecast` |
| **Version** | Snapshot inmutable de un modelo registrado, ligado a un run | versión 7 |
| **Alias** | Etiqueta mutable que apunta a una versión concreta | `@prod` → versión 7 |
| **Artifact Store** | Dónde se guardan los archivos (modelo, plots…) | GCS `mlflow-artifacts/` |

[↑ Volver al índice](#índice)

---

# 4. Logging

## Librería y formato

El proyecto usa la librería estándar de Python `logging` — sin dependencias externas como `structlog` o `loguru`. Lo que sí tiene de custom es un **formatter JSON** definido en `src/common/logging_setup.py`.

Cada línea de log sale como un objeto JSON en una sola línea:

```json
{"timestamp": "2026-04-23T03:00:12.345Z", "severity": "INFO", "logger": "src.training.train", "message": "Training complete — MAE: 1.8432"}
```

El motivo de usar JSON en lugar del formato de texto clásico (`%(asctime)s - %(name)s - %(levelname)s - %(message)s`) es que **Cloud Logging** (el servicio de logs de GCP) parsea JSON automáticamente y crea campos estructurados filtrables. Con texto plano, el campo `message` sería un string opaco. Con JSON, `severity` aparece como nivel del log en la UI de GCP, `logger` es filtrable directamente, y es posible hacer queries como `jsonPayload.logger="src.ingestion.main"`.

## Cómo se usa

### Punto de entrada (`setup_logging`)

Los scripts que arrancan como procesos independientes (Cloud Run Job, `ingestion/main.py`, la API FastAPI) llaman a `setup_logging()` al inicio:

```python
from src.common.logging_setup import setup_logging
setup_logging()   # configura el root logger con JSON output
```

Esto limpia cualquier handler previo del root logger y añade un `StreamHandler` a `stdout` con el `JsonFormatter`. Todos los loggers del proceso heredan esa configuración.

### Módulos internos (`logging.getLogger`)

El resto de módulos no llaman a `setup_logging` — solo obtienen un logger nombrado:

```python
import logging
logger = logging.getLogger(__name__)  # nombre = "src.training.registry"
```

El nombre `__name__` hace que cada módulo tenga su propio logger con el nombre del módulo Python, lo que permite filtrar logs por módulo en Cloud Logging sin configuración adicional.

### Airflow

El scheduler de Airflow configura su propio logging internamente. Las tareas que llaman a código de `src/` a través de `BashOperator` arrancan un proceso nuevo, por lo que `setup_logging()` se ejecuta de forma independiente al arrancar el módulo. Las tareas con `PythonOperator` (como `register_and_promote`) corren dentro del proceso del scheduler, donde Airflow ya ha configurado el root logger — `setup_logging()` no se llama ahí; se deja que Airflow gestione el formato.

[↑ Volver al índice](#índice)

---

# 5. Pre-commit

## Qué es y para qué sirve

`pre-commit` es un framework que ejecuta una serie de comprobaciones automáticamente antes de cada `git commit`. Si alguna falla, el commit se bloquea y hay que corregir el problema primero. Esto evita que lleguen al repositorio archivos con errores de estilo, imports sin usar o archivos de debug.

La configuración está en `.pre-commit-config.yaml` en la raíz del repositorio.

## Hooks configurados

### `pre-commit-hooks` — comprobaciones básicas de ficheros

Hooks de utilidad general que no requieren instalar nada extra:

| Hook | Qué comprueba |
|---|---|
| `trailing-whitespace` | Elimina espacios al final de cada línea |
| `end-of-file-fixer` | Asegura que todos los archivos terminan con exactamente un salto de línea |
| `check-yaml` | Valida que los archivos `.yaml`/`.yml` tienen sintaxis válida |
| `check-toml` | Valida `pyproject.toml` y otros TOML |
| `check-merge-conflict` | Detecta marcadores de conflicto (`<<<<<<`, `=======`) que se hayan quedado sin resolver |
| `debug-statements` | Detecta `pdb.set_trace()`, `breakpoint()` y similares olvidados en el código |

### `ruff` — linting y formateo

[Ruff](https://docs.astral.sh/ruff/) es un linter y formatter de Python escrito en Rust, compatible con las reglas de flake8, isort y black pero órdenes de magnitud más rápido. Se configura en `pyproject.toml`.

Se ejecutan dos hooks:
- **`ruff`** con `--fix`: detecta y corrige automáticamente problemas de estilo — imports sin usar, variables no usadas, orden de imports, etc. Si encuentra algo que no puede corregir solo, bloquea el commit.
- **`ruff-format`**: formatea el código con el estilo de Ruff (equivalente a black). Modifica los archivos in-place.

### `mypy` — comprobación de tipos

Mypy analiza los type hints del código Python y detecta errores de tipos antes de ejecutar el código. Está configurado con `--ignore-missing-imports` para no fallar en librerías sin stubs de tipos, y con stubs explícitos para las dependencias que los necesitan (`pydantic`, `pydantic-settings`, `types-requests`, `types-python-dateutil`).

## Instalación y uso

```bash
# Instalar pre-commit y activar los hooks en el repo local
make setup          # equivale a: pip install pre-commit && pre-commit install

# Ejecutar todos los hooks manualmente sobre todos los archivos
pre-commit run --all-files

# Ejecutar solo un hook concreto
pre-commit run mypy --all-files
```

Una vez instalado con `pre-commit install`, los hooks se ejecutan automáticamente en cada `git commit`. El comando `make lint` también los ejecuta manualmente sin necesidad de hacer un commit.

[↑ Volver al índice](#índice)

---

# 6. Flujo de entrenamiento e inferencia

## Visión general

```
Cada día a las 03:00 UTC
    Airflow DAG bicimad_training
        │
        ├─ Tarea 1: Cloud Run Job (bicimad-training)
        │       Descarga datos de BQ → entrena LightGBM → evalúa → guarda en GCS
        │
        └─ Tarea 2: PythonOperator en la VM de Airflow
                Descarga modelo de GCS → registra en MLflow → promueve a @prod si mejora

Cada 15 minutos
    Airflow DAG bicimad_ingestion
        │
        ├─ Captura estado de estaciones + meteorología → GCS + BQ
        └─ Batch inference: carga modelo @prod → predice → escribe en BQ predictions
```

---

## Artifact Registry y la imagen de training

**Artifact Registry** es el registro de imágenes Docker de GCP (equivalente a Docker Hub pero privado y dentro del mismo proyecto). En este proyecto hay un repositorio llamado `bicimad` en la región `europe-west1` que almacena la imagen de entrenamiento bajo el nombre `bicimad/training`.

La imagen se construye a partir de `infra/training/Dockerfile`:

```dockerfile
FROM python:3.11-slim
COPY pyproject.toml ./
COPY src/ src/
RUN pip install -e ".[training,features,ingestion]"
ENTRYPOINT ["python", "-m", "src.training.train"]
```

El punto clave es el `ENTRYPOINT`: cuando el contenedor arranca, ejecuta directamente `src/training/train.py` como script. No hay ningún servidor escuchando — el contenedor hace su trabajo y termina. Esto es el patrón de uso de Cloud Run Jobs (frente a Cloud Run Services, que sí mantienen un servidor HTTP activo).

Cada merge a `main` que toca `src/` o `pyproject.toml` dispara el workflow de CI/CD `deploy-training.yml`, que construye la imagen y la sube a Artifact Registry con dos tags: `:latest` (que es el que usa el Cloud Run Job) y `:{short-sha}` para trazabilidad.

---

## El pipeline de entrenamiento (Cloud Run Job)

El Cloud Run Job recibe como argumento `--end-date` con la fecha de ayer (pasada desde el DAG de Airflow con `{{ macros.ds_add(ds, -1) }}`). A partir de ahí ejecuta estos pasos:

### 1. Construcción del dataset (expanding window)

`build_training_dataset()` en `src/features/build_dataset.py` consulta BigQuery para obtener los snapshots entre `start_date` y `end_date`. El rango se calcula en `train.py` como `end - (train_days + val_days + test_days)`, que con los valores por defecto son los **9 días anteriores** a la fecha de corte. Es una ventana deslizante de tamaño fijo — la ventana se mueve hacia adelante con cada entrenamiento pero su tamaño no crece.

Se cargan `feature_warmup_days` días adicionales antes del `start_date` real para que las features de lag y rolling tengan valores válidos desde el primer día del split de training (si no se cargaran, las features `dock_bikes_same_time_1w` o `avg_dock_same_hour_7d` estarían a `null` los primeros 7 días).

### 2. Split temporal

`temporal_split()` en `src/training/split.py` divide el dataset en tres fragmentos **cronológicos**, nunca aleatorios:

```
──────────────────────────────────────────────────────── tiempo ──▶
│        train (7 días por defecto)       │  val (1d) │ test (1d) │
```

El split aleatorio está prohibido en series temporales porque introduciría *data leakage*: el modelo vería el futuro durante el entrenamiento.

### 3. Entrenamiento LightGBM

`train_model()` en `src/training/train.py` entrena un regresor LightGBM cuyo objetivo es minimizar el MAE directamente (`objective: regression_l1`). El conjunto de validación se usa exclusivamente para *early stopping* — LightGBM para de añadir árboles cuando el error de validación deja de mejorar durante 50 rondas seguidas, lo que evita el sobreajuste sin necesidad de fijar `n_estimators` manualmente.

Opcionalmente, con `--optuna`, se ejecuta `train_with_optuna()` que busca los mejores hiperparámetros (`num_leaves`, `learning_rate`, `subsample`, etc.) mediante búsqueda bayesiana con Optuna (50 trials por defecto), y luego reentrena con los mejores parámetros encontrados.

### 4. Evaluación en test: dos comparaciones

`evaluate()` en `src/training/evaluate.py` calcula MAE, RMSE, MAE normalizado y R² sobre el conjunto de **test** (el fragmento más reciente, no visto durante el entrenamiento ni el early stopping).

Además de las métricas brutas, computa la comparación contra el **baseline naive**: la predicción más simple posible es asumir que el estado en t+1h será igual que en t (es decir, `dock_bikes(t+60 min) ≈ dock_bikes(t)`). Si el modelo no supera esa predicción trivial, no merece ser desplegado.

El resultado de `evaluate()` incluye:

| Métrica | Qué mide |
|---|---|
| `mae` | Error absoluto medio del modelo (bicicletas) |
| `baseline_mae` | MAE del modelo naive (persistencia) |
| `improvement_pct` | `(baseline_mae - mae) / baseline_mae × 100` |
| `rmse`, `r2`, `mae_normalized` | Métricas adicionales de calidad |

### 5. Guardado en GCS

`save_model()` en `src/training/registry.py` escribe en GCS bajo `models/v{YYYYMMDD_HHMMSS}/`:
- `model.txt` — el modelo LightGBM en formato nativo
- `metadata.json` — métricas, nombres de features, número de árboles, top features
- `feature_importance.json` y `feature_importance.png` — importancia por ganancia y splits

El Cloud Run Job termina aquí. No toca MLflow directamente.

---

## Registro en MLflow y promoción a @prod

Una vez que el Cloud Run Job termina correctamente, el DAG de Airflow lanza la segunda tarea: `register_and_promote` como `PythonOperator` en la VM de Airflow (que sí tiene acceso de red al servidor de MLflow).

Esta tarea llama a `register_and_promote()` en `src/training/registry.py`, que:

1. Descarga el modelo recién entrenado de GCS.
2. Abre un run en MLflow, loguea parámetros, métricas y artefactos, y registra el modelo en el Model Registry creando una nueva versión numerada.
3. Compara el MAE del nuevo modelo con el MAE del modelo actualmente en `@prod` (obtenido del mismo MLflow).
4. Promueve el nuevo modelo al alias `@prod` solo si su MAE es menor. Si no hay ningún modelo en `@prod` todavía (primer entrenamiento), promueve siempre.

Resultado: `@prod` apunta siempre al mejor modelo evaluado sobre el mismo conjunto de test de la última semana.

Para los detalles de cómo funciona el Registry, los aliases y el `run_id`, ver la sección **MLflow en este proyecto** más arriba.

---

## Inferencia batch (cada 15 minutos)

La inferencia no corre en un servicio dedicado — ocurre dentro del DAG de ingesta `bicimad_ingestion`, como una fase más del ciclo de captura de datos.

Después de escribir el snapshot de estaciones en BigQuery, `ingest()` en `src/ingestion/main.py` llama a `predict_all_stations()` en `src/serving/predict.py`. Esta función:

1. Carga el modelo en producción llamando a `load_prod_model()`, que resuelve el alias `@prod` en MLflow y descarga el `model.txt` de GCS. El modelo se cachea en memoria — las siguientes llamadas dentro del mismo proceso de Airflow no vuelven a descargarlo.
2. Construye las features para cada estación activa usando el mismo código de `src/features/` que se usó en training (garantizando que no hay *training-serving skew*).
3. Produce un vector de predicciones: `dock_bikes(t+60 min)` por estación.
4. Escribe las predicciones en la tabla `predictions` de BigQuery, con timestamp de cuándo se hizo la predicción y cuándo se espera que sea válida (`target_timestamp = now + 60 min`).

Si el modelo no está disponible (sistema recién desplegado, MLflow caído), la fase de inferencia falla de forma **no fatal**: el ciclo de ingesta continúa y escribe los datos de estaciones en BQ igualmente. El campo `predictions_written` del resumen del ciclo valdrá 0.

Tras la inferencia, el ciclo lanza también la **reconciliación**: busca predicciones hechas hace ~1 hora y las compara con el estado real actual de las estaciones, calculando el error por ciclo y escribiéndolo en `cycle_metrics`.

---

## API de serving (consulta de predicciones)

La API de serving es una aplicación **FastAPI** en `src/serving/app.py`. No genera predicciones — lee la tabla `predictions` de BigQuery y expone los resultados ya calculados por el batch.

Tres endpoints GET:

| Endpoint | Descripción |
|---|---|
| `GET /health` | Liveness check. Siempre devuelve 200 si el proceso está vivo |
| `GET /predictions/latest` | Devuelve la predicción más reciente de **todas** las estaciones |
| `GET /predictions/{station_id}` | Devuelve la predicción más reciente de **una** estación concreta |

Para arrancar localmente:

```bash
make serve   # → http://localhost:8000
```

La API es actualmente una herramienta de consulta local/dev. No está desplegada como servicio permanente en producción.

[↑ Volver al índice](#índice)

---

# 7. Ingesta

## Qué hace y cuándo se ejecuta

La ingesta captura el estado en tiempo real de las 634 estaciones de BiciMAD y los datos meteorológicos actuales, los persiste en GCS y BigQuery, y acto seguido lanza la inferencia batch para ese ciclo. Se ejecuta cada 15 minutos mediante el DAG `bicimad_ingestion`, que lanza un `BashOperator` que llama a `python -m src.ingestion.main`. El uso de `BashOperator` en vez de `PythonOperator` es deliberado: arranca un proceso hijo desde cero (~100 MB de RAM) en lugar de hacer `fork()` del scheduler de Airflow, que ya ocupa ~2 GB (ver la sección de Airflow para el detalle).

Toda la lógica vive en `src/ingestion/main.py`, en la función `ingest()`, que ejecuta 6 fases en orden. Las fases 1–4 son fatales (si fallan, el DAG reintenta). Las fases 5 y 6 están envueltas en `try/except` y son no-fatales.

---

## Fase 1 — Autenticación con la API EMT

La API de BiciMAD usa un sistema de tokens temporales. En cada ciclo, antes de pedir datos, hay que disponer de un token válido.

Las credenciales (email y contraseña) se leen siempre de **Google Secret Manager** — nunca de variables de entorno ni archivos en disco. Esto lo hace `get_emt_credentials()` en `bicimad_client.py`, que llama a la API de Secret Manager con las credenciales de la service account.

El login en sí es una petición GET a `/v2/mobilitylabs/user/login/` con email y contraseña como cabeceras HTTP. La API devuelve código `"00"` (token nuevo) o `"01"` (token existente extendido) — ambos son válidos. Cualquier otro código es un error. Si el login falla, se reintenta hasta 3 veces con backoff exponencial (2s, 4s).

Para no autenticarse en cada uno de los ~96 ciclos diarios, el token se persiste en disco mediante `TokenCache`: un archivo JSON en `/tmp/.bicimad_token_cache.json` que almacena el token y el timestamp de cuando se obtuvo. El TTL configurado es de 23 horas (el token real expira a las 24h, se usa 23h como margen). En cada ciclo, `get_valid_token()` lee el cache, comprueba la edad, y solo hace login si ha expirado. La ruta del cache es configurable con `BICIMAD_TOKEN_CACHE_PATH`.

## Fase 2 — Fetch de datos

Con el token en mano, se hacen dos peticiones de forma secuencial:

**Estaciones** (`fetch_stations`): GET a `/v2/transport/bicimad/stations/` con el token como cabecera `accessToken`. La respuesta JSON se valida con el schema Pydantic `BicimadApiResponse` definido en `src/common/schemas.py`. Si el campo `code` de la respuesta no es `"00"`, se lanza un error. Si falla la conexión o el servidor devuelve un 5xx, se reintenta hasta 3 veces con backoff exponencial.

**Meteorología** (`fetch_current_weather`): GET a la API pública de Open-Meteo (sin autenticación). Si falla por cualquier motivo, se captura la excepción y se loggea como `WARNING` — el ciclo continúa con `weather = None`. El campo `weather_snapshot` en BigQuery quedará `null` en ese ciclo.

## Fase 3 — Construcción del payload raw

Antes de persistir, se construye un único dict JSON que combina el timestamp del ciclo, el resultado completo de la respuesta de estaciones y el snapshot meteorológico (o `null`). Este payload es la copia fiel de lo que devolvió la API, sin transformaciones.

## Fase 4 — Escritura a GCS y BigQuery

**GCS** (`write_raw_to_gcs`): el payload JSON se sube a GCS en la ruta particionada `raw/station_status/dt=YYYY-MM-DD/hh=HH/mm=MM/{timestamp}.json`. Esta copia raw es el *data lake* inmutable — si en el futuro hay que re-procesar los datos con una lógica diferente, el raw siempre está ahí.

**BigQuery** (`load_to_bigquery`): se construye una fila por estación, añadiendo `ingestion_timestamp` y `weather_snapshot` a los campos de la estación. Las ~634 filas se insertan en la tabla `station_status_raw` mediante *streaming insert* (la API de BQ `insertAll`), que es inmediato pero no transaccional. Las filas son visibles en consultas en cuestión de segundos.

## Fase 5 — Inferencia batch (no fatal)

Descrita en detalle en la sección **Flujo de entrenamiento e inferencia**. En resumen: se carga el modelo `@prod` desde MLflow (cacheado en memoria entre reintentos dentro del mismo proceso), se calculan las features para las estaciones activas, y las predicciones se escriben en BQ `predictions`. Si falla — modelo no disponible, MLflow caído, error de features — se loggea el error y el ciclo continúa.

## Fase 6 — Reconciliación (no fatal)

`reconcile_predictions()` en `src/monitoring/reconcile.py` busca en BQ las predicciones cuyo `target_time` coincide con el timestamp del ciclo actual (es decir, predicciones hechas hace ~1h que dijeron "a las HH:MM habrá X bicis"). Las compara con los valores reales observados en este ciclo. Calcula MAE, RMSE, p50, p90 y la peor estación **en memoria** (sin guardar los errores por estación) y escribe una única fila agregada en `cycle_metrics`. Si no hay predicciones para ese `target_time` (primeras horas tras el despliegue), devuelve `None` silenciosamente.

[↑ Volver al índice](#índice)

---

# 8. Monitorización

## Qué monitoriza el sistema y cuándo

Hay dos capas de monitorización con frecuencias distintas:

- **En tiempo real, cada 15 minutos**: reconciliación de predicciones (fase 6 del DAG de ingesta, descrita arriba). Genera una fila en `cycle_metrics` por ciclo.
- **Una vez al día, a las 06:05 UTC**: el DAG `bicimad_daily_monitoring` ejecuta tres módulos en secuencia: métricas diarias agregadas, drift report, y alertas.

El DAG diario se lanza a las 06:05 (no a las 06:00) para dar margen a que el entrenamiento nocturno de las 03:00 haya terminado antes de que las alertas comparen contra el modelo `@prod`.

---

## Métricas diarias (`daily_metrics.py`)

Agrega los errores de predicción de ayer consultando BigQuery directamente, sin cargar datos en memoria:

```sql
SELECT p.station_id, AVG(ABS(p.predicted_dock_bikes - s.dock_bikes)) AS daily_mae, ...
FROM predictions p
JOIN station_status_raw s ON p.station_id = s.id AND p.target_time = s.ingestion_timestamp
WHERE DATE(p.target_time) = @target_date
GROUP BY p.station_id
```

Este JOIN entre `predictions` y `station_status_raw` es la reconciliación a nivel diario: para cada predicción, busca el snapshot real en el momento en que la predicción decía que sería válida (`target_time`). Produce dos tipos de resultados:

- **Por estación** → tabla `station_daily_metrics` (una fila por estación por día). Permite detectar estaciones problemáticas de forma consistente.
- **Global** → tabla `daily_totals` (una fila por día). Es el KPI principal del sistema: el MAE diario agregado de todas las estaciones.

---

## Drift report (`drift_report.py`)

Detecta si la distribución de las features en los datos recientes se ha desviado respecto a los datos con que se entrenó el modelo. Un drift sostenido indica que el modelo podría estar haciendo predicciones sobre una distribución que nunca vio durante el entrenamiento.

Usa la librería **Evidently** con el preset `DataDriftPreset`, que aplica tests estadísticos (Kolmogorov-Smirnov para variables continuas, chi-cuadrado para categóricas) a cada feature y determina si la distribución actual difiere significativamente de la referencia.

Las dos ventanas de datos:

- **Ventana actual**: los datos de ayer. Se cargan snapshots de BQ con `feature_warmup_days` días previos para que las features de lag/rolling tengan valores válidos, y luego se filtran al día objetivo.
- **Ventana de referencia**: el training window del último modelo entrenado. Se obtiene leyendo `metadata.json` del modelo en GCS (`load_latest_metadata()`), que contiene el campo `saved_at`. La referencia va desde `saved_at - train_days` hasta `saved_at`.

Para que el cálculo sea manejable en la VM de Airflow (e2-medium, 4 GB), la ventana de referencia se muestrea a 100 estaciones aleatorias antes de construir las features — 100 estaciones × 7 días × 96 snapshots ≈ 67K filas, suficiente para la detección estadística de drift.

El resultado se sube a GCS en dos formatos:
- `monitoring/drift/YYYY-MM-DD.html` — informe completo de Evidently, visualizable en el navegador.
- `monitoring/drift/YYYY-MM-DD_summary.json` — resumen ligero con `n_drifted_features`, `share_drifted`, y `drifted_feature_names`. Es el que leen las alertas sin necesidad de parsear el HTML.

---

## Alertas (`alerts.py`)

Dos checks independientes que se ejecutan después del drift report:

### Alerta de rendimiento

Compara el **MAE online de las últimas 24 horas** (promedio de `cycle_metrics` en BQ) contra el **MAE de training del modelo en `@prod`** (obtenido de MLflow via `get_prod_model_metrics()`):

```
si  MAE_online > MAE_training × 1.20  →  PERFORMANCE ALERT
```

El umbral del 20% de degradación tolera variaciones normales por cambios estacionales o días atípicos. Si se supera, puede indicar que el modelo ha envejecido y necesita reentrenamiento urgente, o que hay un problema en el pipeline de features.

### Alerta de drift

Lee el JSON summary generado por `drift_report.py` en GCS y comprueba `share_drifted`:

```
si  share_drifted > 0.30  →  DRIFT ALERT
```

Si más del 30% de las features han cambiado su distribución, hay riesgo de degradación futura aunque el MAE online todavía sea aceptable.

### Qué hacen las alertas cuando se disparan

Loggean un mensaje `WARNING` con `PERFORMANCE ALERT` o `DRIFT ALERT` en el texto. Esos mensajes aparecen en los logs del DAG de Airflow y, si el SMTP está configurado en `airflow.env`, se envía un email al operador (Airflow tiene soporte nativo de alertas por email en `default_args`). **No hay integración con sistemas externos** como PagerDuty o Slack en la implementación actual.

[↑ Volver al índice](#índice)

---

# 9. Servidor MCP (Model Context Protocol)

## Qué es un MCP server, a grandes rasgos

MCP (Model Context Protocol) es un protocolo abierto que estandariza cómo un modelo de lenguaje accede a datos y acciones externas a él. Antes de MCP, cada integración modelo↔herramienta se construía a medida (un conector distinto para cada combinación de modelo y sistema externo). MCP define un contrato único — JSON-RPC 2.0 sobre un transporte común — para que cualquier **servidor** que lo hable pueda conectarse a cualquier **cliente/host** que también lo hable, sin acoplamiento entre ambos.

Tres roles distintos, fáciles de confundir al principio:

| Rol | Qué es | Ejemplo en este proyecto |
|---|---|---|
| **Servidor MCP** | Expone datos/acciones de un dominio concreto. No sabe nada del modelo que lo va a usar. | `src/mcp/server/server.py` (`bicimad`) |
| **Cliente MCP** | Habla el protocolo con un servidor: lista sus capacidades, llama tools, lee resources. No decide nada por sí mismo. | La clase `Client` del SDK, usada tanto en `repl.py` como en `agent.py` |
| **Host** | La aplicación que usa uno o varios clientes MCP y, opcionalmente, pone un LLM en el bucle para decidir qué llamar. | `src/mcp/client/agent.py` (host hecho a mano, API de Anthropic directa); Claude Code y Claude Desktop (hosts ya construidos por Anthropic) |

El transporte en este proyecto es **stdio**: el host lanza el servidor como subproceso y ambos se hablan por `stdin`/`stdout` con mensajes JSON-RPC 2.0, uno por línea. Por eso `stdout` es un canal sagrado — cualquier `print()` o logger mal configurado que escriba ahí rompe el framing del protocolo de forma silenciosa. Todo el logging del servidor va explícitamente a `stderr` (ver `src/mcp/server/server.py`, que llama a `setup_logging(stream=sys.stderr)`).

## Tools, Resources y Prompts — tres mecanismos con intención distinta

MCP no ofrece un único tipo de "capacidad" — separa tres, cada una con una semántica de uso diferente aunque dos de ellas puedan, en teoría, devolver el mismo JSON:

| | Tools | Resources | Prompts |
|---|---|---|---|
| **Quién decide usarla** | El modelo, durante el bucle `tool_use` | El *host*, como contexto — el modelo no puede "pedir" un resource | El usuario, explícitamente |
| **Qué representa** | Una acción o cómputo | Datos de solo lectura | Una plantilla de petición parametrizada |
| **Mecanismo de error** | `raise ToolError(...)` → resultado normal con `is_error=True`; el modelo lo ve y puede reaccionar | `raise ResourceNotFoundError/ResourceError(...)` → excepción `MCPError` de protocolo (`-32602`/`-32603`); el cliente tiene que capturarla con `try/except` | Igual que resources — también vía `MCPError` |
| **En este servidor** | `get_station_status`, `search_stations`, `find_nearest_stations`, `forecast_availability` | `bicimad://stations`, `bicimad://stations/{station_id}`, `bicimad://model-card` | `plan_trip`, `station_report` |

La distinción tools/resources quedó demostrada de forma empírica (no solo documental) al ejecutar la evaluación de la Fase 5 (`tests/test_mcp/eval_results.md`): preguntas que pedían exactamente lo que expone `bicimad://model-card` o `bicimad://stations` resultaron en que el modelo respondiera "no tengo esa herramienta" en vez de inventar una respuesta — porque ningún resource se carga automáticamente en el bucle `tool_use`, solo si el host decide meterlo en el contexto de antemano (en `agent.py`, con el comando manual `/stations`).

## El servidor MCP de este proyecto

### Ubicación y capas

```
src/mcp/
├── server/
│   ├── data_layer.py   # Sin nada de MCP — StationCatalog, get_latest_forecast,
│   │                    # get_model_metadata. Testeable con pytest puro.
│   ├── errors.py        # Excepciones de dominio agnósticas de MCP:
│   │                    # StationNotFoundError, ModelUnavailableError,
│   │                    # ForecastUnavailableError
│   └── server.py        # MCPServer("bicimad") + tools/resources/prompts.
│                         # Único punto donde las excepciones de dominio se
│                         # traducen a ToolError/ResourceError.
└── client/
    ├── repl.py           # Cliente manual sin LLM — tú tecleas los comandos
    └── agent.py          # Host con bucle agéntico — Claude decide qué llamar
```

`errors.py` nunca importa `mcp` — es deliberado. Mantener las excepciones de dominio agnósticas del protocolo permite reutilizar `data_layer.py` fuera de MCP (de hecho, ya reutiliza `src/serving/predictions_query.py`, compartida con la API FastAPI) y deja en `server.py` la única responsabilidad de traducir "esto pasó en el dominio" a "esto es lo que el protocolo necesita ver":

```python
@mcp.tool(name="get_station_status", ...)
def get_station_status(station_id: int) -> StationInfo:
    try:
        return _catalog.get(station_id)
    except StationNotFoundError as e:
        raise ToolError(str(e)) from e
```

Sin ese `try/except`, el modelo solo vería `"Error executing tool get_station_status"` — el SDK descarta el texto original de cualquier excepción que no sea `ToolError` por seguridad (evita filtrar detalles internos en errores no controlados), y loguea el traceback completo a `ERROR` en el servidor.

### Capa de datos — de dónde salen los datos reales

`data_layer.py` no inventa una fuente de datos nueva — reutiliza lo que ya existe en el proyecto:

- **`StationCatalog`**: lee la última partición de `station_status_raw` (la misma tabla que usa la ingesta) y la cachea en memoria con TTL de 15 minutos (`BICIMAD_MCP_CACHE_TTL_SECONDS`), refresco perezoso en la siguiente llamada tras expirar.
- **`get_latest_forecast`**: llama a `src/serving/predictions_query.py::load_latest_prediction_for_station` — la misma función que usa el endpoint `GET /predictions/{station_id}` de la API FastAPI. El servidor MCP nunca llama a esa API por HTTP; importa la función directamente, evitando acoplar su disponibilidad a que la API esté levantada.
- **`get_model_metadata`**: consulta MLflow vía `MlflowClient` para leer metadata del alias `@prod` (versión, MAE, `run_id`) **sin cargar el booster de LightGBM** — el servidor MCP nunca ejecuta `.predict()`, así que no hay motivo para deserializar el modelo en memoria.

### Clientes: dos formas de hablar con el mismo servidor

| | `repl.py` | `agent.py` |
|---|---|---|
| Quién decide qué llamar | Tú, a mano (`call get_station_status {"station_id": 1}`) | El modelo, vía `tool_use` |
| Objetivo | Aislar bugs de protocolo de bugs de un LLM — verificar el servidor por sí mismo | Bucle agéntico real: `messages.create(..., tools=...)` → `tool_use` → `call_tool()` → `tool_result` → repetir |
| Traducción MCP↔Anthropic | Ninguna — habla MCP directamente | `_mcp_tools_to_anthropic()`: traduce `ListToolsResult.tools` al formato `tools=[...]` de la API de Anthropic |

Ambos lanzan `server.py` igual: como subproceso con `StdioServerParameters(command=sys.executable, args=["-m", "src.mcp.server.server"], cwd=repo_root)`. El mismo `server.py`, sin modificar, también puede registrarse como servidor MCP en Claude Code o Claude Desktop (hosts que Anthropic ya construyó) — es la prueba de que el servidor no está acoplado a ninguna implementación concreta de cliente, el objetivo central de MCP como protocolo de interoperabilidad.

Diseño completo, decisiones tomadas y fases de implementación en `docs/PLAN_MCP.md`.

[↑ Volver al índice](#índice)

---

# 10. Arquitecturas de despliegue de un servidor MCP

## stdio no es la única forma — es la más simple

El protocolo MCP define varios **transportes** posibles entre cliente y servidor; `stdio` (el que usa este proyecto) es solo uno de ellos, y el que impone menos estructura. Que cliente y servidor "vivan en el mismo sitio" en `bicimad` no es un límite del protocolo — es una decisión de diseño concreta, adecuada a este caso de uso, y documentada como tal en `docs/PLAN_MCP.md` ("Decisiones ya tomadas").

| | **stdio** (este proyecto) | **HTTP remoto** (Streamable HTTP) |
|---|---|---|
| Relación cliente↔servidor | 1:1 — un proceso servidor por sesión de cliente | N:1 — un servidor sirve a muchos clientes simultáneos |
| Cómo arranca el servidor | El host lo lanza como subproceso (`docker run --rm -i` / `python -m ...`) cuando lo necesita | Proceso ya levantado de antemano, escuchando en un puerto, con su propio ciclo de vida (`restart: unless-stopped`) |
| Canal de comunicación | `stdin`/`stdout` del propio subproceso — no hay red de por medio | Peticiones HTTP sobre la red, con sesión mantenida vía cabeceras |
| Quién puede conectarse | Solo el proceso que lo lanzó — aislamiento total por diseño | Cualquier cliente que alcance la URL — requiere autenticación/autorización explícita (API keys, OAuth) |
| Estado entre llamadas | Vive en memoria del proceso mientras dura la sesión; muere con ella (ver `StationCatalog` de este proyecto, cacheado en memoria del proceso del servidor) | Tiene que sobrevivir a múltiples conexiones/reinicios de cliente — normalmente externalizado (BD, caché compartida) |
| Encaja de serie en `docker-compose.yml` | No — no hay puerto que exponer ni healthcheck `curl` que comprobar | Sí — es un servicio más, igual que la API FastAPI o MLflow en este mismo repo |
| Casos de uso típicos | Herramientas CLI, plugins de IDE (Claude Code, Claude Desktop) — el cliente y el servidor corren en la misma máquina del usuario | Servidor MCP compartido por un equipo/organización, o un backend al que se conectan múltiples apps cliente (web, móvil) a la vez |

## Por qué `bicimad` usa stdio

El servidor MCP de este proyecto corre en la misma VM de GCP que MLflow, y los clientes que lo usan (`repl.py`, `agent.py`, o Claude Code/Desktop) también corren ahí o se conectan a esa VM — no hay necesidad de que terceros externos a la organización lo consuman. Con un único cliente por sesión, `stdio` evita de un plumazo dos problemas que sí tendría que resolver una versión HTTP: autenticación (¿quién tiene permiso para llamar `forecast_availability`?) y aislamiento de estado entre sesiones concurrentes (el `StationCatalog` cacheado en memoria asume implícitamente que solo hay un cliente hablando con ese proceso a la vez).

La decisión no es permanente ni "la única correcta" — está explícitamente marcada como revisable en el plan si el caso de uso cambia. El ejemplo que la propia discusión de diseño usó para probarlo: si el cliente MCP viviera dentro de una app de chatbot en un móvil (en vez de en este mismo repo, como ahora), tendría más sentido un servidor HTTP remoto compartido por todos los usuarios de la app — ahí sí habría N clientes distintos (uno por usuario de móvil) hablando con el mismo servidor, lo que es exactamente el escenario para el que `stdio` no está pensado.

## Qué cambiaría técnicamente al migrar a HTTP

No es un simple cambio de `transport="stdio"` a `transport="http"` sin más consecuencias — implica rediseñar varias decisiones que hoy `bicimad` resuelve "gratis" por ser 1:1:

- **`StationCatalog` y el caché de `get_model_metadata`** (hoy en memoria del proceso, ver sección 9) pasarían a compartirse entre clientes concurrentes — correcto para datos de solo lectura como este catálogo, pero habría que revisar cualquier estado que no deba compartirse entre sesiones.
- **Autenticación**: `stdio` no necesita ninguna — el aislamiento lo da el sistema operativo (quién puede lanzar el subproceso). HTTP sí, porque cualquiera que alcance el puerto puede intentar conectarse.
- **Despliegue**: pasaría de "se lanza por sesión" a vivir en `infra/docker-compose.yml` como un servicio más, con su propio healthcheck — igual que la API FastAPI o MLflow en este mismo repo.

Está marcado explícitamente como "Extensión futura" fuera del alcance del plan actual (`docs/PLAN_MCP.md`, sección "Extensiones futuras") — el objetivo de este proyecto es aprender bien el ciclo de vida de una sesión `stdio` antes de añadir esa complejidad.

[↑ Volver al índice](#índice)

---

# 11. stdout y stderr en el transporte stdio de MCP

## Qué son, a nivel de sistema operativo

Todo proceso arranca con tres canales de E/S ya abiertos, identificados por un número (*file descriptor*):

| Canal | fd | Para qué está pensado |
|---|---|---|
| `stdin` | 0 | Entrada — lo que el proceso lee |
| `stdout` | 1 | Salida **principal** del programa — su resultado real |
| `stderr` | 2 | Salida de **diagnóstico** — logs, warnings, errores |

La distinción no es solo una convención de buen estilo: son dos flujos *independientes*, y cualquier programa (o quien lo lanza) puede redirigirlos por separado. Es lo que permite hacer `comando > salida.txt 2> errores.txt`, o encadenar `comando1 | comando2` — ese `|` conecta el `stdout` de `comando1` con el `stdin` de `comando2`, sin tocar `stderr` para nada. Por diseño, `stdout` es lo que un programa produce para que *otro programa* lo consuma; `stderr` es lo que produce para que lo vea un *humano* (o un sistema de logging), y por eso sigue siendo visible en la terminal aunque `stdout` esté redirigido a un fichero.

## Por qué esto importa especialmente en MCP con transporte stdio

Cuando un host lanza un servidor MCP como subproceso (`StdioServerParameters` en este proyecto — ver sección 9), el `stdout` del proceso hijo **deja de ser "salida para un humano"** y pasa a ser, literalmente, el canal del protocolo: cada mensaje JSON-RPC 2.0 que el servidor envía al cliente se escribe ahí, y el cliente lee ese flujo byte a byte esperando encontrar únicamente JSON-RPC válido.

```
┌─────────────────────┐    stdout (subproceso) = stdin (host)
│ server.py (hijo)      │ ──────────────────────────────────▶  cliente lee y parsea JSON-RPC
│   mcp.run()            │
│   logging → stderr    │ ──────────────────────────────────▶  visible en la terminal/logs
└─────────────────────┘    stderr (subproceso), fuera del protocolo
```

Si cualquier cosa dentro del servidor escribe algo que no sea un mensaje JSON-RPC a `stdout` — un `print()` de depuración olvidado, una librería de terceros con un logger mal configurado que por defecto manda a `stdout` — esa línea se mezcla con el flujo del protocolo. El cliente intenta parsearla como JSON-RPC, falla, y el fallo es **silencioso y difícil de depurar**: no explota en el `print()` que lo causó, sino más tarde, en el primer intento de parseo de ese byte contaminado, con un error que no apunta a la causa real. Este riesgo está anotado explícitamente en `docs/PLAN_MCP.md` ("Riesgos" → "Contaminación de stdout en transporte stdio"), con una advertencia concreta sobre librerías de GCP/MLflow, que a veces logean a `stdout` por defecto.

Por eso `src/mcp/server/server.py` hace esto explícitamente al arrancar:

```python
setup_logging(stream=sys.stderr)
```

`setup_logging()` (en `src/common/logging_setup.py`, ver sección 4) escribe a `sys.stdout` por defecto — correcto para `app.py`, los DAGs y los scripts batch, donde no hay ningún protocolo que proteger. Se parametrizó el `stream` precisamente para que el servidor MCP pudiera pedir `stderr` sin tocar el comportamiento de ningún otro consumidor de esa función.

## Un matiz importante: esto solo aplica al *servidor*, no a cualquier proceso que hable MCP

Es fácil pensar que "en MCP, nunca se puede hacer `print()`" — no es así. La regla es más estrecha: **el `stdout` del proceso que actúa como servidor en una conexión stdio concreta** es el que hay que proteger, porque es el que su *cliente* está leyendo como protocolo. El `stdout` del cliente/host (el proceso de más arriba, el que el humano tiene abierto en su propia terminal) es un `stdout` normal y corriente — nada lo está parseando como JSON-RPC.

Esto se ve con claridad comparando los dos lados en este proyecto:

- `src/mcp/server/server.py` (el servidor): **nunca** hace `print()` — todo pasa por `logging`, dirigido a `stderr`.
- `src/mcp/client/repl.py` y `src/mcp/client/agent.py` (los clientes/hosts): usan `print()` libremente — para mostrar el resultado de una tool, la respuesta del modelo, o (con `--verbose` en `repl.py`) el JSON-RPC crudo que cruza el cable. Ese `stdout` es la terminal del usuario, no el canal hacia ningún otro proceso que lo vaya a parsear.

El criterio de aceptación de la Fase 1 del plan lo verifica justo en este sentido: redirigir el `stdout` del *servidor* a un fichero mientras se le manda una petición manual, y confirmar que no aparece ahí ningún byte que no sea JSON-RPC válido.

[↑ Volver al índice](#índice)

---

# 12. Manejo de errores: de errors.py a ToolError

## Primero, lo básico: qué es una excepción en Python

Cuando algo va mal dentro de una función de Python — un dato que no existe, una división entre cero, una llamada de red que falla — el lenguaje no "devuelve un error" como valor normal. En vez de eso, **lanza una excepción**: crea un objeto que representa "esto ha ido mal" y detiene inmediatamente la ejecución de esa función, devolviendo el control hacia quien la llamó. Si quien la llamó tampoco se ocupa de ese objeto, sigue subiendo hacia quien llamó a ese, y así sucesivamente — a esto se le llama **propagar** la excepción. Si nadie la "atrapa" en todo el camino, el programa entero se detiene y Python imprime un *traceback* (la cadena de llamadas que llevó hasta el error).

```python
def buscar_estacion(id):
    if id not in catalogo:
        raise ValueError(f"no existe la estación {id}")   # lanza la excepción, aquí termina la función
    return catalogo[id]

estacion = buscar_estacion(9999)   # si no se atrapa, el programa se detiene aquí
```

Para no dejar que el programa se detenga, se envuelve el código que puede fallar en un bloque `try`, y se indica qué hacer si ocurre un tipo concreto de excepción con `except`:

```python
try:
    estacion = buscar_estacion(9999)
except ValueError as e:
    print(f"algo falló: {e}")   # el programa continúa, no se detiene
```

`as e` simplemente le da un nombre a ese objeto-excepción, para poder leer su mensaje (`str(e)`) o inspeccionarlo. Un detalle importante para lo que viene después: `except ValueError` solo atrapa excepciones de ese tipo exacto (o subclases suyas) — si la función lanzara un `TypeError`, este bloque lo dejaría pasar de largo, sin atraparlo. Esto es lo que permite tratar distintos tipos de fallo de forma distinta, en vez de un `except` genérico que atrape "cualquier cosa".

## El problema que resuelve este diseño: el host necesita saber *qué* pasó, no solo *que* pasó algo

Cuando una tool de un servidor MCP falla, el SDK tiene que decidir qué le cuenta al cliente (y, a través de él, al modelo). Aquí hay una tensión real: contar el mensaje de error tal cual, sin filtrar, es arriesgado — una excepción inesperada podría filtrar detalles internos (una query SQL completa, una ruta de fichero del servidor, credenciales en un mensaje de librería). Por eso el SDK de MCP, **por defecto**, trata cualquier excepción no reconocida como un fallo opaco: el cliente solo ve `"Error executing tool <nombre>"`, sin el mensaje original, y el servidor loguea el traceback completo solo en su propio `stderr` (ver sección 11).

El problema es que, sin ningún ajuste, **esto también se aplica a errores de dominio perfectamente normales y esperables** — "la estación 9999 no existe" no es un fallo de infraestructura ni un riesgo de seguridad, es información que el modelo podría usar para, por ejemplo, pedirle al usuario un id distinto. Si ese mensaje se descarta igual que un fallo real de BigQuery caído, el modelo se queda ciego ante algo que sí podía haber manejado.

La solución del SDK es un segundo tipo de excepción, `ToolError`, que es la **única** que se trata de forma distinta: si una tool lanza `ToolError(mensaje)`, el cliente recibe ese `mensaje` tal cual, marcado como `is_error=True` — no un crash, sino un resultado normal con una bandera de error. El modelo lo ve en la conversación y puede reaccionar (pedir otro id, disculparse, sugerir una alternativa). Cualquier otra excepción sigue yendo por la vía del mensaje genérico. Así que el diseño que hay que construir en `server.py` tiene un objetivo muy concreto: **que los errores que el modelo sí puede aprovechar lleguen como `ToolError`, y que los que no, sigan tratándose como crash** — nunca al revés.

## `errors.py`: un vocabulario de errores de dominio que no sabe nada de MCP

```python
class StationNotFoundError(Exception):
    pass

class ModelUnavailableError(Exception):
    pass

class ForecastUnavailableError(Exception):
    pass
```

Son clases de excepción **a medida**, cada una heredando directamente de `Exception` (la clase base de la que heredan todas las excepciones en Python) — con esto basta para que `except StationNotFoundError` las distinga del resto. `data_layer.py` (sección 9) lanza estas excepciones cuando detecta el problema correspondiente — por ejemplo, `StationCatalog.get()` lanza `StationNotFoundError` si el id no está en el catálogo cargado en memoria.

El detalle deliberado es que **`errors.py` no importa el paquete `mcp` en absoluto**. Son excepciones de Python normales, sin ninguna relación con el protocolo. Esto tiene dos razones: la capa de datos (`data_layer.py`) queda testeable con `pytest` puro sin montar nada de MCP alrededor (como ya se prueba en `tests/test_mcp/test_data_layer.py`), y esas mismas excepciones podrían, en teoría, reutilizarse si mañana existiera otra forma de exponer estos datos que no fuera MCP — nada en su diseño las ata al protocolo.

## `server.py`: el único lugar donde se traduce una cosa en la otra

Aquí es donde entra el patrón que aparece en cada tool, usando `get_station_status` como ejemplo real del repo:

```python
@mcp.tool(name="get_station_status", ...)
def get_station_status(station_id: int) -> StationInfo:
    try:
        return _catalog.get(station_id)
    except StationNotFoundError as e:
        raise ToolError(str(e)) from e
```

Paso a paso, para quien no esté familiarizado con `try/except`:

1. `try:` envuelve la llamada real al dominio (`_catalog.get(station_id)`). Si todo va bien, su resultado se devuelve normalmente y el `except` ni se evalúa.
2. Si `_catalog.get()` lanza `StationNotFoundError`, la ejecución salta inmediatamente al bloque `except StationNotFoundError as e` — `e` es el objeto de esa excepción, y `str(e)` es su mensaje de texto (p. ej. *"No existe la estación 9999"*).
3. `raise ToolError(str(e)) from e` hace dos cosas a la vez: **lanza una excepción nueva**, de un tipo distinto (`ToolError`, que sí entiende el SDK de MCP), reutilizando el mismo texto; y **la encadena** a la excepción original con `from e` — esto no cambia qué ve el cliente, pero conserva en el traceback interno la causa original, útil si algún día hay que depurar esto desde los logs del servidor.

El resultado: lo que el cliente MCP recibe no es `StationNotFoundError` (que el SDK no conocería, y trataría como crash) ni el error genérico — es exactamente el mensaje de dominio, pero empaquetado en el único tipo de excepción que el SDK sabe traducir a `is_error=True`. Si `get_station_status` tuviera un bug real no previsto (por ejemplo, un `TypeError` por un dato corrupto), ese `except StationNotFoundError` no lo atraparía — seguiría propagándose sin traducir, y el SDK lo trataría correctamente como un crash con traceback en el servidor. Esa es la frontera que todo el diseño protege: solo se traduce lo que se decidió explícitamente que el modelo puede aprovechar.

## El mismo patrón, pero distinto mecanismo, para resources

Para los *resources* (sección 9) el objetivo final es el mismo — que el error de dominio llegue traducido, no como mensaje genérico — pero el mecanismo del SDK es distinto:

```python
try:
    return _catalog.get(sid).model_dump(mode="json")
except StationNotFoundError as e:
    raise ResourceNotFoundError(str(e)) from e
```

Aquí `ResourceNotFoundError` no produce un resultado con una bandera `is_error=True` como `ToolError` — provoca que el **cliente reciba una excepción real de protocolo** (`MCPError`), que hay que capturar explícitamente con su propio `try/except MCPError` alrededor de `read_resource()` (así lo hacen tanto `repl.py` como `agent.py`). Son dos mecanismos del protocolo distintos para el mismo propósito de fondo: que el tipo y el contenido del error lleguen al host de forma utilizable, en vez de perderse detrás de un mensaje genérico — confirmado en vivo ejecutando el servidor real, no solo leído en la documentación del SDK (ver `docs/PLAN_MCP.md`, Fase 2).

## Resumen: el camino completo de un error

| Paso | Qué pasa |
|---|---|
| 1. Dominio | `data_layer.py` detecta el problema y lanza una excepción de `errors.py` (`StationNotFoundError`...) — sin saber nada de MCP |
| 2. Traducción (único lugar) | `server.py` atrapa esa excepción concreta con `try/except` y la relanza como `ToolError` (tools) o `ResourceNotFoundError`/`ResourceError` (resources) |
| 3a. Si es `ToolError` | El SDK lo convierte en un resultado normal con `is_error=True` y el mensaje intacto — el modelo lo ve en la conversación |
| 3b. Si es `ResourceNotFoundError`/`ResourceError` | El SDK lo convierte en una excepción `MCPError` que el cliente debe capturar explícitamente |
| 3c. Si es cualquier otra excepción no traducida | El SDK la trata como un crash: mensaje genérico para el cliente, traceback completo solo en los logs del servidor (`stderr`) |

[↑ Volver al índice](#índice)

---

# 13. Qué es un REPL y por qué se ha construido aquí

## Qué es un REPL

REPL son las siglas de **R**ead-**E**val-**P**rint-**L**oop: un programa que repite, indefinidamente, cuatro pasos — lee lo que escribes, lo evalúa (ejecuta algo con ello), imprime el resultado, y vuelve a empezar. No es un concepto específico de MCP ni de este proyecto: el propio intérprete de Python al que se entra escribiendo `python` sin argumentos es un REPL (lees una línea, la ejecuta, imprime el resultado, repite), igual que una consola de `node`, o un cliente de línea de comandos de una base de datos (`psql`, `sqlite3`). La característica común es la interacción **turno a turno**, sin un script predefinido de antemano — tú decides qué pasa en cada vuelta.

`src/mcp/client/repl.py` aplica exactamente esa idea al protocolo MCP: en vez de evaluar expresiones de Python, cada vuelta del bucle lee un comando que tú escribes (`tools`, `call get_station_status {"station_id": 1}`, `read bicimad://stations`...) y lo traduce a una llamada real al servidor MCP — `list_tools()`, `call_tool()`, `read_resource()`. No hay ningún modelo de lenguaje de por medio; **tú** eres quien decide qué tool llamar y con qué argumentos, escribiéndolo a mano.

## Por qué se ha construido como un paso propio, antes del bucle agéntico

El motivo no es simplemente "hay que tener un cliente de prueba" — es una decisión deliberada de **secuenciar el aprendizaje en capas independientes**. Un servidor MCP conectado a un modelo (`agent.py`, Fase 4) tiene dos fuentes de fallo completamente distintas que, si se prueban juntas desde el principio, son difíciles de distinguir:

1. **Fallos de protocolo/transporte**: un `resource` mal definido, un error que no se traduce a `ToolError` correctamente (sección 12), el *framing* de `stdio` roto por un `print()` donde no debía (sección 11), un JSON Schema mal generado a partir de un type hint...
2. **Fallos de decisión del modelo**: el LLM elige la tool equivocada, construye mal los argumentos, no encadena las llamadas en el orden correcto.

Si el primer cliente que se prueba contra el servidor ya tiene un modelo en el bucle, y algo falla, no hay forma rápida de saber cuál de las dos causas es la responsable. `repl.py` elimina la segunda variable por completo: tú decides exactamente qué tool se llama y con qué argumentos, así que si algo se rompe, **tiene que ser el servidor o el protocolo** — no puede ser una mala decisión de un modelo, porque no hay ningún modelo decidiendo nada. Es el mismo principio que ya aparece en la Fase 0 del plan (`docs/PLAN_MCP.md`): aislar la lógica de negocio de la capa de transporte antes de mezclar ambas.

El flag `--verbose` de `repl.py` (sección 11) lleva esta idea un paso más allá: no solo prueba el servidor a mano, sino que **muestra literalmente** los mensajes JSON-RPC crudos que cruzan el cable — ver `id`, `method`, `params` tal cual viajan, en vez de solo leer sobre ellos en la especificación, es lo que convierte "leer la spec" en "entender el protocolo de verdad" (la distinción que ya quedó documentada al descubrir que el SDK usa `server/discover` en vez del `initialize`/`initialized` clásico — ver sección 9 y Fase 1 del plan).

## ¿Es esto algo habitual en un proyecto MCP real?

No exactamente con este nombre ni como entregable obligatorio — pero la necesidad que cubre sí es real y universal: todo SDK de MCP serio viene con un **MCP Inspector** (`uv run mcp dev server.py` en este proyecto), una herramienta gráfica que hace básicamente lo mismo que `repl.py` — listar y llamar tools/resources/prompts a mano, sin modelo — y que normalmente sustituye a escribir un REPL propio en un proyecto productivo. Construir `repl.py` aquí, en vez de depender solo del Inspector, es una elección deliberadamente educativa: escribir con tus propias manos el código que llama a `list_tools()`, `call_tool()`, `read_resource()` obliga a entender la forma exacta de cada respuesta (`CallToolResult.is_error`, `.content`, `.structured_content`...) de un modo que clicar botones en una UI no exige.

## ¿Deja de tener sentido una vez existe `agent.py`?

No — y esto merece aclararse porque no es obvio a priori. `repl.py` no "evoluciona" hacia `agent.py` ni queda obsoleto cuando el bucle agéntico existe; se quedan como **dos herramientas paralelas con propósitos distintos**, ambas vigentes (de hecho, ambas siguen en el repo, sección 9). Precisamente porque `repl.py` aísla el servidor del modelo, sigue siendo la forma más rápida de descartar una causa cuando algo falla en `agent.py`: si una tool se comporta raro desde el cliente agéntico, repetir la misma llamada a mano en `repl.py` dice de inmediato si el problema está en el servidor (se reproduce igual sin modelo) o en cómo el modelo decidió llamarla (no se reproduce, porque a mano con los argumentos correctos funciona). Perder `repl.py` después de construir `agent.py` significaría perder esa capacidad de diagnóstico por capas que fue, desde el principio, la razón de construirlo.

[↑ Volver al índice](#índice)
