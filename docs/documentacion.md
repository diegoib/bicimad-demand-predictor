# Infraestructura GCP con Terraform

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

---

# Apache Airflow en este proyecto

## Qué es Airflow y para qué sirve aquí

Apache Airflow es un **orquestador de pipelines**: no ejecuta lógica de negocio, sino que decide *cuándo* y *en qué orden* ejecutar código que vive en otro sitio. En este proyecto, todo el código real está en `src/`; los DAGs son la capa fina que lo invoca según un calendario.

En este proyecto Airflow se encarga de dos pipelines:
- **Ingesta** (`bicimad_ingestion`): cada 15 minutos, llama a `src/ingestion/main.py` para capturar el estado de las 634 estaciones de BiciMAD y los datos meteorológicos y escribirlos en GCS y BigQuery.
- **Entrenamiento** (pendiente): semanalmente, construirá el dataset de features, entrenará el modelo LightGBM y lo registrará en GCS.

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
| **CI/CD** | Ninguno todavía | Deploy automático de DAGs en cada merge a main |
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
| `BICIMAD_BQ_PROJECT` | Project ID de GCP para las queries de BigQuery |
| `GOOGLE_APPLICATION_CREDENTIALS` | Ruta a la clave JSON de la service account dentro del contenedor. Las librerías de Google Cloud la detectan automáticamente |
