# BiciMAD Demand Predictor

Sistema batch de ML para predecir la disponibilidad de bicicletas en las estaciones de BiciMAD (Madrid) con un horizonte de 60 minutos.

## Stack

- **Modelo**: LightGBM (modelo global, todas las estaciones)
- **Features**: 29 features en 5 grupos (lag, temporal, meteorológica, histórica, estática)
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

Crea un fichero `.env` en la raíz con:

```
BICIMAD_EMT_EMAIL=tu_email@ejemplo.com
BICIMAD_EMT_PASSWORD=tu_contraseña
BICIMAD_ENV=dev
```

Regístrate en el [portal de la EMT](https://openapi.emtmadrid.es/) para obtener credenciales.

### Comandos principales

```bash
make ingest-local   # Descarga datos reales de BiciMAD y Open-Meteo
make features       # Construye el dataset de features
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
