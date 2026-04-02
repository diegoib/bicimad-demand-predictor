# Session Handoff — BiciMAD Demand Predictor

## Proyecto
Sistema batch de ML para predecir disponibilidad de bicis en estaciones BiciMAD (Madrid) con horizonte t+60min. Stack: LightGBM, Airflow, FastAPI, GCP (GCS + BigQuery), Pydantic v2. Monorepo en `/workspace`.

---

## Estado actual: Fase 1 completa, Fase 5.1/5.2/7.1 completa

### Completado

**Fase 0 — Scaffolding** ✅
- Estructura de directorios, `pyproject.toml`, `Makefile`, `src/common/` (config, schemas, logging).

**Fase 1 — Ingesta** ✅
- `src/ingestion/bicimad_client.py`: auth via `GET /v2/mobilitylabs/user/login/` con email+password como **headers HTTP**. Token expira 24h; `TokenCache` persiste en `data/.token_cache.json` en dev, se regenera en cada invocación en prod. Credenciales leídas de `os.environ` (dev) o Google Secret Manager (prod) — **nunca en `Settings`**.
- `src/ingestion/weather_client.py`: Open-Meteo con 8 variables horarias: `temperature_2m`, `apparent_temperature`, `precipitation`, `precipitation_probability`, `wind_speed_10m`, `weather_code`, `is_day`, `direct_radiation`.
- `src/ingestion/storage.py`: `write_raw_to_local`, `write_raw_to_gcs`, `load_to_bigquery`. Particionado: `station_status/dt=YYYY-MM-DD/hh=HH/mm=MM.json`.
- `src/ingestion/main.py`: orquesta todo. Entry points: `ingest()`, `handler()` (Cloud Function), `__main__`. **No hay modo mock** — los tests cubren eso.
- 35 tests en `tests/test_ingestion/`. `make ingest-local` funciona y escribe en `data/raw/`.

**Fase 5 (parcial) — Airflow** ✅
- `infra/docker-compose.yml`: Airflow 2.9.3 LocalExecutor (webserver + scheduler + postgres). Monta `src/` y `dags/` como volúmenes; `PYTHONPATH=/opt/airflow/project`.
- `dags/ingestion_dag.py`: schedule `*/15 * * * *`, un `PythonOperator` que llama `ingest()`, 3 reintentos con backoff exponencial.

**Fase 7 (parcial) — GCP Infra** ✅
- `infra/terraform/`: GCS bucket con lifecycle 1 año, BigQuery dataset `bicimad` + tabla `station_status_raw` con schema explícito y partición diaria, VM e2-small con Docker preinstalado, service account `bicimad-ingestion` con permisos mínimos, Secret Manager secrets `bicimad-emt-email` y `bicimad-emt-password`.

---

## Decisiones clave tomadas en esta sesión

| Decisión | Motivo |
|---|---|
| `StationSnapshot.id: int` (no `str`) | La API real devuelve enteros (e.g. 1409) |
| Credenciales EMT fuera de `Settings` | Son secrets; se leen via `os.environ` o Secret Manager |
| `.env` usa `EMAIL`/`PASSWORD` (sin prefijo) | Convención del usuario; `get_emt_credentials()` acepta ambos formatos |
| `extra="ignore"` en `Settings` | El `.env` tiene vars sin prefijo `BICIMAD_` que pydantic-settings rechazaba |
| Sin modo `mock` en `ingest()` | Redundante con los tests unitarios que mockean HTTP |
| Devcontainer corre como `root` | Entorno personal; simplifica instalación de herramientas |
| `~/.claude` montado como volumen | Persiste historial de Claude Code al reconstruir el container |
| Terraform instala Terraform en devcontainer | Se necesita para `terraform apply` desde el IDE |

---

## Schemas actualizados (respecto al diseño inicial)

**`WeatherSnapshot`** añade 4 campos: `apparent_temperature`, `precipitation_probability`, `is_day`, `direct_radiation`.

**`FeatureRow`** añade 7 campos weather: las 4 directas + derivadas `feels_cold` (apparent_temp < 8°C), `feels_hot` (apparent_temp > 30°C), `high_solar_radiation` (direct_radiation > 400 W/m²).

**`StationSnapshot`**: `id: int`, `model_config = {"extra": "ignore"}` para tolerar campos extra de la API.

**`FeatureRow.station_id`** y **`PredictionOutput.station_id`**: `int` (antes `str`).

---

## Siguiente fase pendiente: Fase 2 — Feature Engineering

Tareas:
- `src/features/feature_definitions.py`: catálogo de las 36 features organizadas por grupo
- `src/features/holidays.py`: festivos nacionales + Comunidad de Madrid 2024-2027
- `src/features/build_features.py`: funciones `build_lag_features`, `build_temporal_features`, `build_weather_features`, `build_historical_features`, `build_station_features`, `build_all_features`
- `src/features/build_dataset.py`: lee JSONs locales o BigQuery y produce DataFrame con features + target
- Tests en `tests/test_features/`

Nota: hay que acumular ~2-3 semanas de datos antes de que los lag features y estadísticas históricas sean útiles para entrenar.

---

## Comandos útiles

```bash
make ingest-local    # descarga datos reales → data/raw/
make test            # 35 tests, todos pasan
make lint            # ruff + mypy limpios
make airflow-up      # levanta Airflow local (requiere infra/airflow.env + infra/gcp-key.json)

# Desplegar GCP
cd infra/terraform && terraform init && terraform apply -var="project_id=TU_PROJECT"
echo -n "email" | gcloud secrets versions add bicimad-emt-email --data-file=-
echo -n "pass"  | gcloud secrets versions add bicimad-emt-password --data-file=-
terraform output -raw service_account_key_base64 | base64 -d > infra/gcp-key.json
```

---

## Archivos críticos

| Archivo | Rol |
|---|---|
| `src/common/schemas.py` | Fuente de verdad de todos los modelos de datos |
| `src/common/config.py` | Settings con prefijo `BICIMAD_` (sin credenciales EMT) |
| `src/ingestion/bicimad_client.py` | Auth + fetch estaciones |
| `src/ingestion/main.py` | Orquestador de ingesta |
| `dags/ingestion_dag.py` | DAG Airflow cada 15 min |
| `infra/terraform/main.tf` | Infraestructura GCP |
| `PLAN.md` | Checklist de implementación con estado actual |
