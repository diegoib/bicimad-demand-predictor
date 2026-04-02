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
| `google_compute_instance` | VM e2-small con Debian 12, 20 GB disco, IP pública efímera, y un startup script que instala Docker |
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
