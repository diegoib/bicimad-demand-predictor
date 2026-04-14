# BiciMAD Demand Predictor

Sistema batch de ML para predecir la disponibilidad de bicicletas en las estaciones de BiciMAD (Madrid) con un horizonte de 60 minutos.

## Stack

- **Modelo**: LightGBM (modelo global, todas las estaciones)
- **Features**: 36 features en 5 grupos (lag, temporal, meteorológica, histórica, estática)
- **Orquestación**: Apache Airflow (self-hosted con Docker Compose)
- **Infraestructura**: Google Cloud (GCS, BigQuery, Cloud Functions)
- **Serving**: FastAPI

## Inicio rápido

### Requisitos

- Python 3.11+
- Docker (para Airflow)

### Instalación

```bash
make setup
```

### Configuración de credenciales EMT

**Paso 1 — Registro**: Crea una cuenta gratuita en el [Portal de la EMT MobilityLabs](https://openapi.emtmadrid.es/). Recibirás un email y contraseña de acceso.

**Paso 2 — Secret Manager**: Las credenciales se almacenan en Google Secret Manager con los nombres de secreto `bicimad-emt-email` y `bicimad-emt-password`. El sistema los lee automáticamente via Application Default Credentials (ADC).

Para desarrollo local, configura ADC contra el proyecto `bicimad-dev`:

```bash
gcloud auth application-default login
export BICIMAD_GCP_PROJECT=bicimad-dev
```

**Sobre el token**: La API devuelve un `accessToken` que expira cada 24 horas. El sistema lo cachea automáticamente en `/tmp/.bicimad_token_cache.json` (configurable via `BICIMAD_TOKEN_CACHE_PATH`) y lo renueva cuando es necesario.

### Comandos principales

```bash
make features       # Construye el dataset de features desde BigQuery
make train          # Entrena el modelo
make serve          # Levanta la API de predicción
make test           # Ejecuta los tests
make lint           # Linting y type checking
make airflow-up     # Levanta Airflow local
```

## Estructura

```
src/
├── common/       # Schemas, config y logging compartidos
├── ingestion/    # Clientes de API y escritura de datos
├── features/     # Feature engineering
├── training/     # Entrenamiento y evaluación
├── serving/      # API FastAPI
└── monitoring/   # Drift y alertas
dags/             # DAGs de Airflow (orquestación pura)
tests/            # Tests unitarios
infra/            # Docker Compose + Terraform
notebooks/        # Exploración (no producción)
```

## Arquitectura

El sistema sigue un pipeline batch:

1. **Ingesta** (cada 15 min): Cloud Function → GCS → BigQuery
2. **Features**: Transformación de snapshots crudos a features ML
3. **Training** (semanal): Dataset → LightGBM → Model Registry en GCS
4. **Serving**: FastAPI carga el modelo y genera predicciones en real-time
5. **Monitorización**: Reporte de drift Evidently + alertas de degradación

## Documentación

Consulta `docs/design_doc.docx` para decisiones de diseño y `PLAN.md` para el estado de implementación.
