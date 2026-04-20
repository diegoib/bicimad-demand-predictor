-- Creates the MLflow backend database on first postgres startup.
-- For existing deployments (non-empty data volume), run manually:
--   docker compose exec postgres psql -U airflow -c "CREATE DATABASE mlflow;"
SELECT 'CREATE DATABASE mlflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow')\gexec
