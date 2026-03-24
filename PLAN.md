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
- [x] **0.4** Crear `src/common/config.py` con Pydantic Settings (BICIMAD_ENV, BICIMAD_GCS_BUCKET, BICIMAD_BQ_DATASET, BICIMAD_EMT_EMAIL, BICIMAD_EMT_PASSWORD)
- [x] **0.5** Crear `src/common/schemas.py` con los modelos Pydantic:
  - `StationSnapshot`: un registro de estación de la API (dock_bikes, free_bases, total_bases, geometry, id, number, name, activate, no_available)
  - `BicimadApiResponse`: la respuesta completa de la API (code, description, datetime, data: list[StationSnapshot])
  - `WeatherSnapshot`: datos meteorológicos de Open-Meteo (temperature_2m, precipitation, wind_speed_10m, weather_code, timestamp)
  - `FeatureRow`: una fila del dataset de entrenamiento (todas las features + target)
  - `PredictionOutput`: respuesta de la API de serving (station_id, predicted_dock_bikes, prediction_time, model_version)
- [x] **0.6** Crear `src/common/logging_setup.py` con configuración de logging estructurado (JSON en prod, texto en dev)
- [x] **0.7** Crear `.gitignore`, `README.md` (en español, breve), `.pre-commit-config.yaml`
- [x] **0.8** Ejecutar `make lint` y `make test` (aunque no haya tests reales aún, verificar que el setup funciona)

**Done cuando:** `make lint` pasa sin errores, la estructura de directorios existe, los schemas compilan, y un `from src.common.schemas import StationSnapshot` funciona.

---

## Fase 1: Ingesta de datos

- [ ] **1.1** Crear `src/ingestion/bicimad_client.py`:
  - Función `login(email, password) -> access_token` que llama a `/v2/mobilitylabs/user/login/`
  - Función `fetch_stations(access_token) -> BicimadApiResponse` que llama a `/v2/transport/bicimad/stations/`
  - Manejo de errores: reintentos con backoff exponencial (3 intentos), validación del código de respuesta ("00" = ok)
  - Cache del token (expira en 24h)
- [ ] **1.2** Crear `src/ingestion/weather_client.py`:
  - Función `fetch_current_weather(lat, lon) -> WeatherSnapshot` que llama a Open-Meteo `/v1/forecast`
  - Parámetros: latitude=40.4168, longitude=-3.7038, hourly=temperature_2m,precipitation,wind_speed_10m,weather_code
  - Extraer la hora actual del forecast (no todo el array de 7 días)
- [ ] **1.3** Crear `src/ingestion/storage.py`:
  - Función `write_raw_to_gcs(data, bucket, prefix, timestamp)` que escribe JSON a GCS particionado
  - Función `write_raw_to_local(data, base_path, timestamp)` para modo local (dev)
  - Función `load_to_bigquery(data, dataset, table)` para carga incremental
  - Particionado: `station_status/dt=YYYY-MM-DD/hh=HH/mm=MM.json`
- [ ] **1.4** Crear `src/ingestion/main.py`:
  - Función `ingest()` que orquesta: login → fetch_stations → fetch_weather → validate → write
  - Entry point para Cloud Function: `def handler(request)` que llama a `ingest()`
  - Entry point para ejecución local: `if __name__ == "__main__"`
- [ ] **1.5** Crear tests:
  - `tests/test_ingestion/test_bicimad_client.py`: mock de la API, verificar parsing y validación
  - `tests/test_ingestion/test_weather_client.py`: mock de Open-Meteo
  - `tests/test_ingestion/test_storage.py`: verificar escritura local y formato de particionado
  - Fixture con un JSON de ejemplo real de la API (usar el extracto del design doc)
- [ ] **1.6** Probar ingesta real en local: ejecutar `make ingest-local` y verificar que se escriben archivos JSON correctos en `data/raw/`
- [ ] **1.7** Documentar en README cómo configurar las credenciales de EMT

**Done cuando:** `make ingest-local` descarga datos reales de BiciMAD y Open-Meteo, los valida con Pydantic, y los escribe en `data/raw/` con el particionado correcto. `make test` pasa todos los tests de ingesta.

---

## Fase 2: Feature engineering

- [ ] **2.1** Crear `src/features/feature_definitions.py`:
  - Diccionario/Enum con las 29 features definidas en el design doc, organizadas por grupo
  - Cada feature tiene: nombre, tipo (int/float/bool/cat), grupo (lag/temporal/meteo/stats/static), descripción
- [ ] **2.2** Crear `src/features/holidays.py`:
  - Lista de festivos nacionales y de la Comunidad de Madrid (2024-2027)
  - Función `is_holiday(date) -> bool`
- [ ] **2.3** Crear `src/features/build_features.py`:
  - Función `build_lag_features(df) -> df`: dock_bikes_now, free_bases_now, occupancy_rate_now, lags (15m, 30m, 1h), delta_15m
  - Función `build_temporal_features(df) -> df`: hour_of_day, day_of_week, is_weekend, month, is_holiday, minutes_since_midnight, is_rush_hour
  - Función `build_weather_features(df) -> df`: temperature_2m, precipitation_mm, wind_speed_10m, is_raining, weather_code
  - Función `build_historical_features(df) -> df`: avg_dock_same_hour_7d, std_dock_same_hour_7d, avg_dock_same_weekday, station_daily_turnover, dock_bikes_same_time_1w
  - Función `build_station_features(df) -> df`: total_bases, station_id, latitude, longitude, distrito
  - Función principal `build_all_features(raw_df) -> df`: compone todas las anteriores y añade el target (dock_bikes en t+60min)
- [ ] **2.4** Crear `src/features/build_dataset.py`:
  - Función `build_training_dataset(source, start_date, end_date) -> DataFrame`: lee datos crudos, aplica build_all_features, elimina filas con NaN en el target
  - Soporte para fuente local (archivos JSON) y BigQuery
- [ ] **2.5** Crear tests:
  - `tests/test_features/test_build_features.py`: datos sintéticos con resultados conocidos
  - Verificar que lag features se calculan correctamente (no data leakage)
  - Verificar que el target se alinea correctamente (dock_bikes de 60min en el futuro)
  - Verificar que features temporales son correctas para fechas conocidas
- [ ] **2.6** Crear notebook de exploración `notebooks/01_eda_features.ipynb`:
  - Distribución de features por grupo
  - Correlación features vs target
  - Patrones temporales (hora del día, día de semana)
  - Impacto de la lluvia en dock_bikes

**Done cuando:** `build_all_features()` transforma datos crudos en un DataFrame con las 29 features y el target correctamente alineado. Tests pasan. No hay data leakage en lag features ni en el target.

---

## Fase 3: Entrenamiento

- [ ] **3.1** Crear `src/training/split.py`:
  - Función `temporal_split(df, val_days=14, test_days=7) -> (train_df, val_df, test_df)`
  - Validar que no hay overlap temporal entre splits
  - Log: fechas de cada split y número de filas
- [ ] **3.2** Crear `src/training/train.py`:
  - Función `train_model(train_df, val_df, feature_cols, target_col) -> lgb.Booster`
  - Configurar LightGBM con station_id como feature categórica
  - Función `train_with_optuna(train_df, val_df, n_trials) -> (best_params, best_model)`
  - Guardar modelo como archivo .txt (LightGBM nativo) y como .pkl (sklearn wrapper)
- [ ] **3.3** Crear `src/training/evaluate.py`:
  - Función `evaluate(model, test_df) -> dict` que calcula: MAE, RMSE, MAE normalizado, mejora vs baseline naive
  - Función `evaluate_critical_states(model, test_df) -> dict`: precisión en predecir estaciones vacías/llenas
  - Función `generate_report(metrics, output_path)`: genera un JSON con todas las métricas y metadata
- [ ] **3.4** Crear `src/training/baseline.py`:
  - Función `naive_baseline(test_df) -> dict`: predice dock_bikes(t+1h) = dock_bikes(t) y calcula MAE/RMSE
  - Esto es la referencia mínima que el modelo debe superar
- [ ] **3.5** Crear `src/training/registry.py`:
  - Función `save_model(model, metrics, version, gcs_bucket)`: guarda modelo + métricas en GCS con versionado
  - Función `load_latest_model(gcs_bucket) -> (model, metadata)`
  - Versionado: `models/v{YYYYMMDD_HHMMSS}/model.txt` + `metadata.json`
- [ ] **3.6** Crear tests:
  - `tests/test_training/test_split.py`: verificar que splits no solapan y están ordenados temporalmente
  - `tests/test_training/test_train.py`: entrenar con datos sintéticos pequeños, verificar que produce un modelo válido
  - `tests/test_training/test_evaluate.py`: métricas con predicciones conocidas
- [ ] **3.7** Crear notebook `notebooks/02_first_model.ipynb`:
  - Entrenar primer modelo con datos acumulados
  - Comparar con baseline naive
  - Feature importance
  - Análisis de errores por estación, hora, y condiciones meteorológicas

**Done cuando:** el pipeline train → evaluate produce un modelo LightGBM con métricas documentadas que supera el baseline naive. El modelo se puede guardar y cargar desde disco/GCS.

---

## Fase 4: Serving

- [ ] **4.1** Crear `src/serving/model_loader.py`:
  - Clase `ModelManager` que carga el modelo desde GCS (o local) y lo cachea
  - Método `reload()` para recargar si hay nueva versión
  - Thread-safe para uso con FastAPI
- [ ] **4.2** Crear `src/serving/app.py`:
  - `GET /health`: healthcheck
  - `GET /predict/{station_id}`: predicción para una estación (genera features en real-time, predice)
  - `GET /predict/all`: predicción batch para todas las estaciones
  - `GET /model/info`: versión del modelo, métricas, fecha de entrenamiento
  - Usar `src/features/build_features.py` para generar features (mismo código que training)
- [ ] **4.3** Crear `src/serving/Dockerfile`:
  - Imagen ligera (python:3.11-slim)
  - Solo dependencias de serving (no training, no airflow)
- [ ] **4.4** Crear tests:
  - `tests/test_serving/test_app.py`: tests con TestClient de FastAPI
  - Verificar que el endpoint devuelve PredictionOutput válido
  - Verificar que features generadas en serving son idénticas a las de training (anti training-serving skew)
- [ ] **4.5** Probar localmente: `make serve` y llamar a los endpoints con curl

**Done cuando:** `make serve` levanta la API, `curl localhost:8000/predict/39` devuelve una predicción válida usando el mismo pipeline de features que el training.

---

## Fase 5: Orquestación con Airflow

- [ ] **5.1** Crear `infra/docker-compose.yml`:
  - Airflow con LocalExecutor (webserver + scheduler + postgres metadata DB)
  - Volumen montado para `dags/`
  - Variables de entorno para conexión a GCP
- [ ] **5.2** Crear `dags/ingestion_dag.py`:
  - Schedule: cada 15 minutos
  - Tasks: check_api → fetch_bicimad → fetch_weather → validate → write_gcs → load_bq
  - Retries: 3 con exponential backoff
  - Alertas por email en fallo
- [ ] **5.3** Crear `dags/training_dag.py`:
  - Schedule: semanal (domingo 03:00)
  - Tasks: build_dataset → split → train → evaluate → compare_with_previous → register_model → drift_report
  - Condicional: solo registrar modelo si mejora al anterior
- [ ] **5.4** Probar localmente: `make airflow-up`, verificar que los DAGs aparecen en el UI y se ejecutan correctamente
- [ ] **5.5** Documentar el setup de Airflow en la VM e2-small de GCP

**Done cuando:** Airflow ejecuta ambos DAGs correctamente en local. El DAG de ingesta escribe datos cada 15 min y el de training produce un modelo versionado.

---

## Fase 6: Monitorización

- [ ] **6.1** Crear `src/monitoring/drift_report.py`:
  - Función que genera reporte Evidently comparando features recientes vs periodo de entrenamiento
  - Data drift + prediction drift
  - Guardar reporte HTML en GCS
- [ ] **6.2** Crear `src/monitoring/alerts.py`:
  - Función que compara MAE actual vs MAE del último entrenamiento
  - Si MAE sube >20%, disparar alerta (email o log)
  - Si drift significativo en features clave, disparar reentrenamiento adelantado
- [ ] **6.3** Integrar en el DAG de training (task `generate_drift_report` ya planificado en 5.3)
- [ ] **6.4** Crear dashboard simple (Streamlit o HTML estático):
  - Mapa de estaciones con predicciones coloreadas
  - Gráfico de MAE histórico
  - Última ejecución de ingesta y training

**Done cuando:** cada entrenamiento semanal genera un reporte de drift y se comparan métricas automáticamente. Las alertas se disparan correctamente cuando se simula un aumento de error.

---

## Fase 7: Despliegue en GCP

- [ ] **7.1** Crear `infra/terraform/` con los recursos GCP:
  - VM e2-small (Airflow)
  - Cloud Storage bucket
  - BigQuery dataset + tablas
  - Cloud Function (ingesta, como backup/alternativa al DAG)
  - Cloud Scheduler jobs
  - IAM service accounts con permisos mínimos
- [ ] **7.2** Configurar GitHub Actions:
  - CI: lint + tests en cada PR
  - CD: deploy de Cloud Function y actualización de DAGs en la VM
- [ ] **7.3** Deploy de Airflow en la VM e2-small
- [ ] **7.4** Verificar que todo funciona end-to-end en GCP
- [ ] **7.5** Documentar runbook: cómo desplegar, cómo recuperarse de fallos, cómo forzar reentrenamiento

**Done cuando:** el sistema funciona autónomamente en GCP — ingesta cada 15 min, entrenamiento semanal, serving activo, alertas configuradas.

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
