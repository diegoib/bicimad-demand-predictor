# PLAN.md — Plan de implementación

Checklist ordenada de tareas. Marca con `[x]` las completadas.
Cada fase tiene un criterio de "done" — no avanzar a la siguiente fase sin cumplirlo.

---

## Fase 0: Scaffolding del repositorio

- [x] **0.1** Crear estructura de directorios: `src/{ingestion,features,training,serving,monitoring,common}`, `dags/`, `tests/`, `infra/`, `docs/`, `notebooks/`
- [x] **0.2** Crear `pyproject.toml` con dependencias organizadas por grupo:
  - core: pydantic, requests, python-dateutil
  - ingestion: google-cloud-storage, google-cloud-bigquery
  - features: pandas, polars (opcional)
  - training: lightgbm, optuna, scikit-learn
  - serving: fastapi, uvicorn
  - monitoring: evidently
  - dev: pytest, ruff, mypy, pre-commit
- [x] **0.3** Crear `Makefile` con los comandos listados en CLAUDE.md
- [x] **0.4** Crear `src/common/config.py` con Pydantic Settings (BICIMAD_GCS_BUCKET, BICIMAD_BQ_DATASET, BICIMAD_BQ_PROJECT, BICIMAD_MODEL_VERSION)
- [x] **0.5** Crear `src/common/schemas.py` con los modelos Pydantic:
  - `StationSnapshot`: un registro de estación de la API (dock_bikes, free_bases, total_bases, geometry, id, number, name, activate, no_available)
  - `BicimadApiResponse`: la respuesta completa de la API (code, description, datetime, data: list[StationSnapshot])
  - `WeatherSnapshot`: datos meteorológicos de Open-Meteo (temperature_2m, precipitation, wind_speed_10m, weather_code, timestamp)
  - `FeatureRow`: una fila del dataset de entrenamiento (todas las features + target)
  - `PredictionOutput`: respuesta de la API de serving (station_id, predicted_dock_bikes, prediction_time, model_version)
- [x] **0.6** Crear `src/common/logging_setup.py` con configuración de logging estructurado (siempre JSON para Cloud Logging)
- [x] **0.7** Crear `.gitignore`, `README.md` (en español, breve), `.pre-commit-config.yaml`
- [x] **0.8** Ejecutar `make lint` y `make test` (aunque no haya tests reales aún, verificar que el setup funciona)

**Done cuando:** `make lint` pasa sin errores, la estructura de directorios existe, los schemas compilan, y un `from src.common.schemas import StationSnapshot` funciona.

---

## Fase 1: Ingesta de datos

- [x] **1.1** Crear `src/ingestion/bicimad_client.py`:
  - Función `login() -> str` que llama a `GET /v2/mobilitylabs/user/login/` con `email` y `password` como **headers HTTP** (no en el body).
  - Credenciales siempre desde **Google Secret Manager** (`bicimad-emt-email`, `bicimad-emt-password`). Dev usa ADC contra `bicimad-dev`.
  - El `accessToken` expira en **24 horas**. Implementar `TokenCache`:
    - Persiste en `/tmp/.bicimad_token_cache.json` (configurable via `BICIMAD_TOKEN_CACHE_PATH`).
    - Apropiado para Airflow en VM e2-medium (disco persistente entre ejecuciones del DAG).
    - Método `get_valid_token() -> str`: lee cache, valida antigüedad < 23h; si no, llama a `login()` y actualiza cache.
  - Función `fetch_stations(access_token: str) -> BicimadApiResponse` que llama a `GET /v2/transport/bicimad/stations/`
  - Manejo de errores: reintentos con backoff exponencial (3 intentos), validación del código de respuesta ("00" = ok)
- [x] **1.2** Crear `src/ingestion/weather_client.py`:
  - Función `fetch_current_weather(lat, lon) -> WeatherSnapshot` que llama a Open-Meteo `/v1/forecast`
  - Parámetros: latitude=40.4168, longitude=-3.7038, con las siguientes variables horarias:
    - Originales: `temperature_2m`, `precipitation`, `wind_speed_10m`, `weather_code`
    - Nuevas: `is_day`, `precipitation_probability`, `direct_radiation`, `apparent_temperature`
  - Extraer la hora actual del forecast (no todo el array de 7 días)
- [x] **1.3** Crear `src/ingestion/storage.py`:
  - Función `write_raw_to_gcs(data, bucket, prefix, timestamp)` que escribe JSON a GCS particionado
  - Función `load_to_bigquery(data, dataset, table)` para carga incremental
  - Particionado: `station_status/dt=YYYY-MM-DD/hh=HH/mm=MM.json`
- [x] **1.4** Crear `src/ingestion/main.py`:
  - Función `ingest()` que orquesta: login → fetch_stations → fetch_weather → validate → write
  - Entry point para Cloud Function: `def handler(request)` que llama a `ingest()`
  - Entry point para ejecución local: `if __name__ == "__main__"`
- [x] **1.5** Crear tests:
  - `tests/test_ingestion/test_bicimad_client.py`: mock de la API, verificar parsing y validación
  - `tests/test_ingestion/test_weather_client.py`: mock de Open-Meteo
  - `tests/test_ingestion/test_storage.py`: verificar escritura local y formato de particionado
  - Fixture con un JSON de ejemplo real de la API (usar el extracto del design doc)
- [x] **1.6** Probar ingesta real: ejecutar `python -m src.ingestion.main` y verificar que los datos llegan a GCS y BigQuery
- [x] **1.7** Documentar en README cómo configurar las credenciales de EMT

**Done cuando:** `python -m src.ingestion.main` descarga datos reales de BiciMAD y Open-Meteo, los valida con Pydantic, y los escribe en GCS y BigQuery con el particionado correcto. `make test` pasa todos los tests de ingesta.

---

## Fase 2: Feature engineering

- [x] **2.1** Crear `src/features/feature_definitions.py`:
  - ~~Diccionario/Enum~~ Pydantic BaseModel (frozen) con las 35 features organizadas por grupo
  - Cada feature tiene: nombre, tipo (int/float/bool/cat), grupo (lag/temporal/meteo/stats/static), descripción
- [x] **2.2** Crear `src/features/holidays.py`:
  - ~~Lista hardcodeada~~ Librería `holidays` (Spain prov="MD") + Nov 9 (Almudena) manual
  - Función `is_holiday(date) -> bool`
- [x] **2.3** Crear `src/features/build_features.py`:
  - Función `build_lag_features(df) -> df`: dock_bikes_now, free_bases_now, occupancy_rate_now, lags (15m, 30m, 1h), delta_15m
  - Función `build_temporal_features(df) -> df`: hour_of_day, day_of_week, is_weekend, month, is_holiday, minutes_since_midnight, is_rush_hour
  - Función `build_weather_features(df) -> df`:
    - Directas: `temperature_2m`, `precipitation_mm`, `wind_speed_10m`, `is_raining`, `weather_code`
    - Nuevas directas: `is_day`, `precipitation_probability`, `direct_radiation`, `apparent_temperature`
    - Derivadas: `feels_cold` (apparent_temperature < 8°C), `feels_hot` (apparent_temperature > 30°C), `high_solar_radiation` (direct_radiation > 400 W/m²)
  - Función `build_historical_features(df) -> df`: avg_dock_same_hour_7d, std_dock_same_hour_7d, avg_dock_same_weekday, station_daily_turnover, dock_bikes_same_time_1w
  - Función `build_station_features(df) -> df`: total_bases, station_id, latitude, longitude, distrito
  - Función principal `build_all_features(raw_df) -> df`: compone todas las anteriores y añade el target (dock_bikes en t+60min)
  - **Librería: Polars** (no pandas)
- [x] **2.4** Crear `src/features/build_dataset.py`:
  - Función `build_training_dataset(start_date, end_date) -> DataFrame`: lee datos de BigQuery, aplica build_all_features, elimina filas con NaN en el target
  - Función `build_serving_dataset() -> DataFrame`: ídem sin filtrar el target nulo (filas de inferencia)
- [x] **2.5** Crear tests:
  - `tests/test_features/test_build_features.py`: datos sintéticos con resultados conocidos (61 tests)
  - Verificar que lag features se calculan correctamente (no data leakage)
  - Verificar que el target se alinea correctamente (dock_bikes de 60min en el futuro)
  - Verificar que features temporales son correctas para fechas conocidas
- [x] **2.6** Crear notebook de exploración `notebooks/01_eda_features.ipynb`:
  - Distribución de features por grupo
  - Correlación features vs target
  - Patrones temporales (hora del día, día de semana)
  - Impacto de la lluvia en dock_bikes

**Done cuando:** `build_all_features()` transforma datos crudos en un DataFrame con las 29 features y el target correctamente alineado. Tests pasan. No hay data leakage en lag features ni en el target.

---

## Fase 3: Entrenamiento

- [x] **3.1** Crear `src/training/split.py`:
  - `temporal_split(df, train_days=28, val_days=1, test_days=1) -> (train_df, val_df, test_df)`
  - Warn (no error) si train < train_days — permite empezar con 7 días mientras se acumulan datos
  - Log: fechas y filas de cada split
- [x] **3.2** Crear `src/training/train.py`:
  - `train_model(train_df, val_df, ...) -> lgb.Booster` con early stopping en val
  - `train_with_optuna(train_df, val_df, n_trials) -> (best_params, best_model)`
  - station_id y distrito como features categóricas; booleans casteados a Int8
  - `__main__`: args --train-days, --optuna, --n-trials, --output-dir
- [x] **3.3** Crear `src/training/evaluate.py`:
  - `evaluate(model, df) -> dict`: MAE, RMSE, R², MAE normalizado, mejora vs baseline
  - `evaluate_critical_states(model, df) -> dict`: precisión/recall de estaciones vacías/llenas
  - `generate_report(metrics, output_path, model)`: JSON con métricas, timestamp, num_trees
- [x] **3.4** Crear `src/training/baseline.py`:
  - `naive_baseline(df) -> dict`: predice dock_bikes_now como target; calcula MAE/RMSE
- [x] **3.5** Crear `src/training/registry.py`:
  - `save_model(model, metrics, output_dir)`: versión v{YYYYMMDD_HHMMSS}/, model.txt + metadata.json
  - `load_latest_model(model_dir)`: carga la versión más reciente
  - Siempre sube a GCS tras guardar; descarga desde GCS si el directorio local está vacío
- [x] **3.6** Crear tests (141 tests totales, todos pasan):
  - `tests/test_training/test_split.py`: 9 tests de no-overlap, tamaños, warnings, errores
  - `tests/test_training/test_train.py`: 9 tests de _prepare_features, train_model, train_with_optuna
  - `tests/test_training/test_evaluate.py`: 17 tests de baseline, evaluate, critical_states, report
  - `tests/test_training/test_registry.py`: 8 tests de save/load model
- [x] **3.7** Crear notebook `notebooks/02_first_model.ipynb`:
  - 8 celdas: load dataset, split, baseline, train LightGBM, evaluar en test, feature importance, MAE por hora, guardar modelo

**Done cuando:** el pipeline train → evaluate produce un modelo LightGBM con métricas documentadas que supera el baseline naive. El modelo se puede guardar y cargar desde disco/GCS.

---

## Fase 4: Serving

- [x] **4.1** Crear `src/serving/model_loader.py`:
  - Clase `ModelManager` que carga el modelo desde GCS (o local) y lo cachea
  - Método `reload()` para recargar si hay nueva versión
  - Thread-safe para uso con FastAPI
  - `prepare_serving_features()`: convierte Polars DataFrame → pandas X sin filtrar target nulo
- [x] **4.2** Crear `src/serving/app.py`:
  - `GET /health`: healthcheck (siempre 200)
  - `GET /predict/{station_id}`: predicción para una estación desde cache en memoria
  - `GET /predict/all`: predicción batch para todas las estaciones
  - `GET /model/info`: versión del modelo, métricas, fecha de entrenamiento
  - `POST /model/reload`: recarga modelo + cache (llamado por el DAG de training)
  - Cache: al arrancar carga `build_serving_dataset()` y guarda una fila por estación en memoria
- [x] **4.3** Crear `src/serving/Dockerfile`:
  - Imagen ligera (python:3.11-slim)
  - Solo dependencias de serving: fastapi, uvicorn, lightgbm, polars, pandas, pydantic, holidays, google-cloud-storage
- [x] **4.4** Crear tests (19 tests, todos pasan — 160 totales):
  - `tests/test_serving/test_app.py`: TestClient + monkeypatch para inyectar estado
  - Cubre: health, model info, predict single/batch, reload, 404/503, schema, prediction_time = snapshot+1h
  - `src/features/build_dataset.py`: añadido `build_serving_dataset()` (sin filtro de target nulo)
- [ ] **4.5** Probar localmente: `make serve` y llamar a los endpoints con curl

**Done cuando:** `make serve` levanta la API, `curl localhost:8000/predict/39` devuelve una predicción válida usando el mismo pipeline de features que el training.

---

## Fase 5: Orquestación con Airflow

- [x] **5.1** Crear `infra/docker-compose.yml`:
  - Airflow con LocalExecutor (webserver + scheduler + postgres metadata DB)
  - Volumen montado para `dags/` y `src/` (PYTHONPATH=/opt/airflow/project)
  - Variables de entorno para conexión a GCP vía `infra/airflow.env`
- [x] **5.2** Crear `dags/ingestion_dag.py`:
  - Schedule: `*/15 * * * *`
  - Tarea única `PythonOperator` que llama a `src.ingestion.main.ingest()`
  - Retries: 3 con exponential backoff (2 min → 10 min max)
  - XCom con `stations_count` y `ingest_timestamp` para observabilidad
  - Nota: `ingest()` incluye internamente las fases predict y reconcile — el DAG no necesita cambios para soportar la inferencia
- [x] **5.3** Crear `dags/training_dag.py`:
  - Schedule: semanal (domingo 03:00)
  - Task 1: `CloudRunExecuteJobOperator` → lanza Cloud Run Job de entrenamiento
  - El job ejecuta: build_dataset → split → train → evaluate → compare_with_previous → register_model → drift_report
  - Condicional: solo registrar modelo si mejora al anterior
  - El entrenamiento corre en Cloud Run Jobs (no en la VM) para no competir con Airflow por RAM
- [x] **5.4** Crear `infra/training/Dockerfile`:
  - Imagen ligera (python:3.11-slim) solo con dependencias de training (polars, lightgbm, optuna, google-cloud-bigquery, google-cloud-storage)
  - Entrypoint: `python -m src.training.train`
- [ ] **5.5** Crear Cloud Run Job en GCP (`gcloud run jobs create` o Terraform):
  - Imagen: imagen Docker del paso anterior publicada en Artifact Registry
  - Región: la misma que la VM
  - Variables de entorno: mismas que la VM (bucket, BQ dataset, project)
- [ ] **5.6** Probar localmente: `make airflow-up`, verificar que los DAGs aparecen en el UI y se ejecutan correctamente
- [ ] **5.7** Documentar el setup de Airflow en la VM e2-medium de GCP
- [x] **5.8** Añadir schemas de predicción a `src/common/schemas.py`:
  - `BatchPredictionRow`: `station_id`, `prediction_made_at` (T), `target_time` (T+60), `predicted_dock_bikes`, `model_version`
  - `CycleMetrics`: `cycle_timestamp`, `model_version`, `n_predictions`, `mae`, `rmse`, `p50_error`, `p90_error`, `worst_station_id`, `worst_station_error`
- [x] **5.9** Crear `src/serving/predict.py`:
  - `predict_all_stations(model, model_version, stations_response, weather, snapshot_timestamp, feature_cols=None) -> list[BatchPredictionRow]`
    - Solo estaciones activas (`activate==1` y `no_available==0`)
    - Llama a `build_all_features()` de `src.features.build_features` y a `_prepare_features()` de `src.training.train` (mismo código que training — cero training-serving skew)
    - `target_time = snapshot_timestamp + timedelta(hours=1)`
  - `_raw_snapshot_to_polars(stations_response, weather, snapshot_timestamp) -> pl.DataFrame` (privada)
    - Produce exactamente las mismas columnas que `_load_json_file` en `build_dataset.py`
    - Si `weather` es `None`, rellena columnas meteorológicas con centinelas (0.0/0/False)
- [x] **5.10** Crear `src/monitoring/reconcile.py`:
  - `reconcile_predictions(current_snapshot, snapshot_timestamp, bq_project, bq_dataset) -> CycleMetrics | None`
    - Consulta BQ tabla `predictions` donde `target_time == snapshot_timestamp`; retorna `None` si no hay filas
    - Calcula MAE, RMSE, p50/p90 de error, worst station directamente en memoria
    - Devuelve `CycleMetrics` listo para insertar (no almacena errores individuales por estación)
  - `compute_cycle_mae(metrics: CycleMetrics) -> float` — acceso directo al campo `mae`
- [x] **5.11** Extender `src/ingestion/storage.py` con 2 funciones nuevas (sin modificar las existentes):
  - `load_predictions_to_bigquery(predictions, project, dataset) -> int` — streaming insert tabla `predictions`
  - `load_cycle_metrics_to_bigquery(metrics, project, dataset) -> int` — streaming insert tabla `cycle_metrics`
- [x] **5.12** Extender `src/ingestion/main.py`: añadir fases predict + reconcile al final de `ingest()`:
  - Cache lazy del modelo a nivel de módulo: `_model_cache: tuple[lgb.Booster, dict] | None = None` + `_get_model()`
  - **Fase predict** (try/except no fatal): `_get_model()` → `predict_all_stations()` → `load_predictions_to_bigquery`
  - **Fase reconcile** (try/except no fatal): `reconcile_predictions()` → si hay métricas: log + `load_cycle_metrics_to_bigquery`
  - Extender dict de retorno: `predictions_written`, `cycle_mae`
  - El DAG de Airflow **no cambia** — toda la lógica vive en `ingest()`
- [x] **5.13** Crear tests (pendiente actualizar para el nuevo diseño de métricas agregadas):
  - `tests/test_serving/test_predict.py`: columnas de `_raw_snapshot_to_polars` idénticas a `_load_json_file`, excluye inactivas, centinelas sin weather; `predict_all_stations`: N activas → N rows, `target_time = prediction_made_at + 1h`, valores finitos, mismos dtypes que training (anti-skew)
  - `tests/test_monitoring/test_reconcile.py`: `reconcile_predictions` (None sin filas BQ, CycleMetrics correcto con MAE/RMSE/p50/p90, worst_station_id correcto, estaciones sin match se omiten)
  - Extender `tests/test_ingestion/test_storage.py`: `TestLoadPredictionsToBigquery` y `TestLoadCycleMetricsToBigquery`
- [x] **5.14** Tablas BQ (en `infra/terraform/`):
  - Tabla `predictions`: partición en `prediction_made_at`, cluster por `station_id`
  - Tabla `cycle_metrics`: partición en `cycle_timestamp`, cluster por `model_version` — una fila por ciclo de ingesta con MAE, RMSE, p50/p90, worst station
  - Tabla `station_daily_metrics`: partición en `date`, cluster por `station_id` — una fila por (estación, día) con MAE diario; escrita por el job diario de monitorización (Fase 6)

**Done cuando:** Airflow ejecuta ambos DAGs correctamente. El DAG de ingesta escribe datos cada 15 min; el DAG de training dispara un Cloud Run Job que produce un modelo versionado en GCS. A partir del segundo ciclo de ingesta, `ingest()` escribe predicciones y (60 min después) métricas agregadas del ciclo en `cycle_metrics`.

---

## Fase 6: Monitorización

Dos frecuencias de monitorización:
- **Cada 15 min** — reconciliación dentro de `ingest()` → `cycle_metrics` (MAE por ciclo, ya en Fase 5)
- **Diario** — job dedicado que agrega errores por estación + totales del día + genera reporte de data drift Evidently
- **Semanal** — reentrenamiento compara MAE online vs MAE de training y decide si registrar nuevo modelo

### Job diario de monitorización (`dags/daily_monitoring_dag.py`)

- [x] **6.1** Crear `src/monitoring/daily_metrics.py`:
  - `compute_station_daily_metrics(date, bq_project, bq_dataset) -> list[StationDailyMetrics]`
    - JOIN `predictions` × `station_status_raw` por `station_id` y `target_time = ingestion_timestamp`
    - GROUP BY `station_id, model_version` → `n_cycles`, `daily_mae`, `daily_rmse`
    - Retorna `[]` si no hay datos
  - `compute_overall_daily_metrics(date, bq_project, bq_dataset) -> OverallDailyMetrics | None`
    - Misma query sin GROUP BY por estación → totales del día (`n_stations`, `n_cycles`, `daily_mae`, `daily_rmse`)
    - Escribe a tabla `daily_totals` (Terraform añadido en Fase 6)
  - Schemas en `src/common/schemas.py`: `StationDailyMetrics` y `OverallDailyMetrics`
  - Bloque `__main__` con `argparse --date` (default: ayer UTC)
- [x] **6.2** Crear `src/monitoring/drift_report.py`:
  - `generate_daily_drift_report(date, bq_project, bq_dataset, gcs_bucket) -> dict`
    - Carga features del día desde BQ via `_load_bigquery_snapshots` + `build_all_features`
    - Ventana de referencia: 28 días antes del `saved_at` del modelo activo (= training window por defecto)
    - Calcula data drift con Evidently (`DataDriftPreset`) — solo distribución de features, sin ground truth
    - Sube a GCS: `monitoring/drift/YYYY-MM-DD.html` (reporte completo) + `YYYY-MM-DD_summary.json` (leído por alertas y dashboard)
    - Retorna dict con `n_drifted_features`, `share_drifted`, `drifted_feature_names`
    - Retorna zeros si no hay datos del día (no lanza excepción)
  - Bloque `__main__` con `argparse --date` (default: ayer UTC)
- [x] **6.3** Crear `src/monitoring/alerts.py`:
  - `check_performance_alert(bq_project, bq_dataset) -> bool`
    - Lee últimas 24h de `cycle_metrics`; compara MAE medio vs `mae` en `metadata.json` del modelo activo
    - Si MAE online > MAE entrenamiento × 1.20: log warning + retorna True
    - Retorna False si no hay datos (no alertar por ausencia)
  - `check_drift_alert(drift_summary: dict) -> bool`
    - Si `share_drifted` > 0.30 (más del 30% de features con drift): log warning + retorna True
  - Bloque `__main__`: lee drift summary del día desde GCS para pasarlo a `check_drift_alert`
- [x] **6.4** Crear `dags/daily_monitoring_dag.py`:
  - Schedule: `5 6 * * *` (06:05 UTC — 5 min de margen respecto al DAG de ingesta que corre en xx:00)
  - Task 1 (`compute_station_metrics`): `python -m src.monitoring.daily_metrics` → escribe a `station_daily_metrics` + `daily_totals`
  - Task 2 (`generate_drift_report`): `python -m src.monitoring.drift_report` → HTML + JSON a GCS
  - Task 3 (`run_alerts`): `python -m src.monitoring.alerts` — depende de tasks 1 y 2
  - `[compute_station_metrics, generate_drift_report] >> run_alerts`
- [x] **6.5** Writers BQ y tablas Terraform:
  - `load_station_daily_metrics_to_bigquery` en `src/ingestion/storage.py`
  - `load_overall_daily_metrics_to_bigquery` en `src/ingestion/storage.py`
  - Tabla `daily_totals` añadida en `infra/terraform/main.tf` (partición `date` DAY, cluster `model_version`)
  - Tabla `station_daily_metrics` ya definida en 5.14 (writer implementado aquí)
  - `load_latest_metadata()` añadido en `src/training/registry.py` (descarga solo `metadata.json`, sin `model.txt`)
- [x] **6.6** Crear `src/monitoring/dashboard.py` — generador HTML estático:
  - Consulta BQ (`cycle_metrics` últimos 7 días, `station_daily_metrics` top-10 peores estaciones)
  - Lee drift summary JSON desde GCS
  - Genera `index.html` con Chart.js (CDN) y sube a GCS: `monitoring/dashboard/index.html`
  - Sin servidor ni dependencias nuevas — acceso via signed URL o bucket público
  - Nota: dashboard Streamlit completo queda para Fase 8.4 en Cloud Run

**Done cuando:** el código está completo y testado (214 tests, lint+mypy limpios). La verificación con datos reales (DAG ejecutándose en Airflow, tablas pobladas, HTML en GCS) queda pendiente hasta el deploy en GCP (Fase 7).

---

## Fase 7: Despliegue en GCP

- [x] **7.1** Crear `infra/terraform/` con los recursos GCP:
  - VM e2-medium (Airflow) con startup script (Docker + Docker Compose)
  - Cloud Storage bucket con lifecycle rule (borrar tras 365 días)
  - BigQuery dataset + 5 tablas (`station_status_raw`, `predictions`, `cycle_metrics`, `station_daily_metrics`, `daily_totals`) con schemas explícitos, partición diaria y clustering
  - Secret Manager secrets `bicimad-emt-email` y `bicimad-emt-password`
  - Service account `bicimad-ingestion` con roles mínimos (GCS objectCreator/Viewer, BQ dataEditor+jobUser, secretAccessor)
- [x] **7.2** Configurar GitHub Actions:
  - CI (`.github/workflows/ci.yml`): lint + tests en cada PR y push a main
  - CD (`.github/workflows/cd.yml`): en merge a main:
    1. Build + push imagen de training a Artifact Registry
    2. `gcloud run jobs update bicimad-training` con el nuevo digest
    3. SSH a VM: `git pull` + `docker compose restart airflow-scheduler`
  - Autenticación via Workload Identity Federation (sin long-lived keys en CI)
  - Secrets necesarios: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `AIRFLOW_VM_IP`, `AIRFLOW_VM_SSH_KEY`
- [x] **7.3** Deploy de Airflow en la VM e2-medium:
  - Nuevos targets en `Makefile`: `airflow-vars`, `deploy-vm`, `run-training-job`
  - Setup inicial: clone repo, copiar `gcp-key.json`, editar `airflow.env`, `make airflow-up`, `make airflow-vars`
  - Pasos manuales adicionales: Artifact Registry, Cloud Run Job, cargar secretos EMT en Secret Manager
  - Ver `docs/runbook.md` § 2 para instrucciones completas paso a paso
- [x] **7.4** Checklist de verificación end-to-end:
  - Ingesta: filas en GCS y `station_status_raw` tras primer ciclo (15 min)
  - Predicciones: filas en `predictions` tras entrenar un modelo y esperar un ciclo
  - Reconciliación: filas en `cycle_metrics` tras ~1 h con predicciones activas
  - Training manual: `gcloud run jobs execute bicimad-training`
  - Monitorización diaria: trigger manual de `bicimad_daily_monitoring`
  - Ver `docs/runbook.md` § 3 para todos los comandos de verificación
- [x] **7.5** Runbook (`docs/runbook.md`):
  - Arquitectura de despliegue (diagrama)
  - Primer despliegue paso a paso (Terraform → secretos → Artifact Registry → Cloud Run Job → VM → GitHub Secrets → activar DAGs)
  - Verificación end-to-end con comandos exactos
  - Operaciones habituales: update de código, reentrenamiento forzado, backfill, ver logs, restart
  - Recuperación de fallos: ingesta, training, VM caída, rollback de datos en BQ
  - Gestión de secretos y rotación

**Done cuando:** el sistema funciona autónomamente en GCP — ingesta cada 15 min, entrenamiento semanal, alertas configuradas. La verificación real requiere ejecutar los comandos de `docs/runbook.md` § 3 contra el proyecto GCP de producción.

---

## Fase 8: Extensiones (opcional)

- [ ] **8.1** Añadir horizontes de predicción: t+30min, t+2h (un modelo por horizonte)
- [ ] **8.2** Features de vecindad: estado de las 3-5 estaciones más cercanas
- [ ] **8.3** Features de eventos: partidos de fútbol, manifestaciones, festivales (scraping o API)
- [ ] **8.4** Dashboard público con Streamlit desplegado en Cloud Run
- [ ] **8.5** Datos históricos: cargar datos 2017-2023 para entrenar modelo con más profundidad temporal
- [ ] **8.6** A/B testing: comparar modelo actual vs modelo con nuevas features

---

## Notas

- **No saltarse fases.** Cada fase depende de la anterior.
- **Cada tarea debe tener tests.** No avanzar sin que `make test` pase.
- **Commit frecuente.** Un commit por tarea completada como mínimo.
- **Cuando haya duda, consultar el design doc** (`docs/design_doc.docx`).
