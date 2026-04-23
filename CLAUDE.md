# CLAUDE.md — Instrucciones para Claude Code

Este repositorio contiene un sistema batch de ML para predecir la disponibilidad de bicicletas en las estaciones de BiciMAD (Madrid).

## Stack y versiones

- Python 3.11+
- LightGBM (modelo principal)
- Optuna (hyperparameter tuning)
- FastAPI (serving)
- Apache Airflow 2.x (orquestación, self-hosted en VM GCP con Docker Compose)
- MLflow (model registry, self-hosted en VM GCP con Docker Compose)
- Google Cloud: Cloud Storage, BigQuery, Cloud Run Jobs, Artifact Registry
- Pydantic v2 (validación de schemas y configuración)
- pytest para tests

## Estructura del repositorio

```
bicimad-demand-forecast/
├── src/
│   ├── ingestion/          # Clientes de API y escritura en GCS/BQ
│   ├── features/           # Feature engineering
│   ├── training/           # Pipeline de entrenamiento y model registry
│   ├── serving/            # API FastAPI
│   ├── monitoring/         # Drift + alertas
│   └── common/             # Código compartido (schemas, config, logging)
├── dags/                   # DAGs de Airflow (orquestación pura)
├── tests/                  # Tests unitarios por módulo
├── infra/                  # Terraform + docker-compose.yml + docker-compose.mlflow.yml
├── docs/                   # Documentación
├── pyproject.toml          # Dependencias unificadas
├── Makefile                # Comandos: make train, make serve, make test…
└── CLAUDE.md               # Este archivo
```

## Convenciones de código

### General
- Código y comentarios en **inglés**.
- Type hints en todas las funciones públicas.
- Docstrings en formato Google style.
- Imports absolutos desde la raíz del paquete: `from src.common.schemas import StationSnapshot`.
- Sin código de negocio en los DAGs de Airflow — los DAGs solo importan y llaman funciones de `src/`.
- Máximo 1 nivel de herencia en clases. Preferir composición y funciones.

### Schemas y contratos de datos
- Todos los schemas de datos se definen en `src/common/schemas.py` con Pydantic v2.
- Los schemas son la fuente de verdad compartida entre ingestion, features, training y serving.
- Cuando se añade o modifica una feature, SIEMPRE actualizar el schema primero.
- Validar datos con Pydantic al entrar (ingesta) y al salir (serving).

### Features
- Toda la lógica de feature engineering vive en `src/features/`.
- `src/features/feature_definitions.py` define la lista canónica de features con nombre, tipo y descripción.
- La misma función de feature engineering se usa en training y serving (evitar training-serving skew).
- Las features se organizan en 5 grupos: lag, temporal, meteorológica, estadística histórica, estática de estación.

### Configuración
- Toda la configuración vive en `src/common/config.py` usando Pydantic Settings.
- Variables de entorno con prefijo `BICIMAD_` (ejemplo: `BICIMAD_GCS_BUCKET`).
- Sin secrets hardcodeados. Usar Secret Manager o variables de entorno.
- Dev y prod usan ambos GCP — dev con proyecto separado (`bicimad-dev`) y Application Default Credentials (`gcloud auth application-default login`). Los tests usan mocks de GCS/BQ.

### Tests
- Tests unitarios en `tests/` reflejando la estructura de `src/`.
- Usar fixtures de pytest para datos de ejemplo (snapshots de estaciones).
- Testear feature engineering con datos conocidos y resultados esperados.
- No testear implementaciones internas de librerías (LightGBM, BigQuery).

## API de BiciMAD

- Endpoint principal: `GET https://openapi.emtmadrid.es/v2/transport/bicimad/stations/`
- Requiere autenticación: header `accessToken` obtenido via `/v2/mobilitylabs/user/login/`
- Respuesta: JSON con campo `data` que es una lista de estaciones.
- Campos clave por estación: `dock_bikes`, `free_bases`, `total_bases`, `no_available`, `geometry.coordinates`, `number`, `name`, `id`, `activate`.
- La API puede devolver código `"01"` o `"02"` en errores. Solo `"00"` es éxito.
- Rate limit: no documentado oficialmente. Usar intervalos de 15 minutos mínimo.

## API de Open-Meteo

- Endpoint: `GET https://api.open-meteo.com/v1/forecast`
- Sin autenticación. Gratuita para uso no comercial.
- Coordenadas Madrid: latitude=40.4168, longitude=-3.7038
- Variables horarias necesarias: `temperature_2m`, `precipitation`, `wind_speed_10m`, `weather_code`
- Devuelve forecast para 7 días en formato JSON.
- Historical API disponible en `/v1/archive` para datos pasados.

## Modelo ML

- Target: `dock_bikes` en t+60 minutos por estación.
- Modelo global (todas las estaciones juntas, station_id como feature categórica).
- Algoritmo: LightGBM regressor.
- Split siempre temporal (nunca aleatorio): train → validation → test.
- Baseline naive: dock_bikes(t+1h) = dock_bikes(t).
- Métrica principal: MAE (Mean Absolute Error).
- Reentrenamiento semanal con expanding window.

## Comandos útiles (Makefile)

```
make setup              # Instalar dependencias y pre-commit hooks
make features           # Construir dataset de features desde BigQuery
make train              # Entrenar modelo (requiere ADC configurado)
make serve              # Levantar API FastAPI en modo desarrollo
make test               # Ejecutar tests
make lint               # Ruff + mypy
make airflow-up         # Levantar Airflow local con Docker Compose
make airflow-down       # Parar Airflow
make mlflow-up          # Levantar MLflow tracking server con Docker Compose
make mlflow-down        # Parar MLflow
make deploy-training    # Build y push imagen de training a Artifact Registry
make run-training-job   # Disparar el Cloud Run Job de training manualmente
```

## Decisiones ya tomadas (no reconsiderar)

- Monorepo (no microservicios separados).
- LightGBM como modelo principal (no deep learning, no Prophet).
- Modelo global (no un modelo por estación).
- Target: dock_bikes absoluto, no tasa de ocupación ni clasificación.
- Airflow self-hosted en VM e2-medium (no Cloud Composer, demasiado caro).
- Entrenamiento del modelo en Cloud Run Jobs (no en la VM de Airflow — RAM limitada). El DAG de training dispara el job vía `CloudRunExecuteJobOperator`.
- Horizonte inicial: t+1h (ampliable después).
- Snapshots cada 15 minutos.

## Cuando no sepas qué hacer

1. Consulta `src/common/schemas.py` para contratos de datos.
2. Si hay ambigüedad, pregunta antes de implementar.
