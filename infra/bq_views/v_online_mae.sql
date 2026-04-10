-- View: v_online_mae
-- Daily MAE and RMSE per model_version from the cycle_metrics table.
-- cycle_metrics already stores aggregated stats per cycle, so this view
-- simply re-aggregates across cycles within each day.
-- Used by src/monitoring/alerts.py to compare online error vs training MAE.
--
-- Usage:
--   bq mk --view "$(cat infra/bq_views/v_online_mae.sql)" \
--         --project_id=YOUR_PROJECT \
--         YOUR_PROJECT:bicimad.v_online_mae
--
-- Or reference from Terraform as a google_bigquery_table with view block.

SELECT
    DATE(cycle_timestamp)                        AS prediction_date,
    model_version,
    COUNT(*)                                     AS n_cycles,
    SUM(n_predictions)                           AS total_predictions,
    -- Weighted average MAE (weighted by number of stations per cycle)
    SUM(mae * n_predictions) / SUM(n_predictions) AS mae,
    SQRT(SUM(rmse * rmse * n_predictions) / SUM(n_predictions)) AS rmse,
    AVG(p50_error)                               AS avg_p50_error,
    MAX(p90_error)                               AS max_p90_error,
    MAX(worst_station_error)                     AS max_station_error
FROM `@PROJECT.@DATASET.cycle_metrics`
GROUP BY
    prediction_date,
    model_version
ORDER BY
    prediction_date DESC,
    model_version DESC
