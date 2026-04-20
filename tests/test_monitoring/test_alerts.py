"""Tests for src/monitoring/alerts.py."""

from unittest.mock import MagicMock, patch

from src.monitoring.alerts import check_drift_alert, check_performance_alert

_PROJECT = "test-project"
_DATASET = "bicimad"

_PROD_METRICS = {"mae": 2.0, "version": "3", "run_id": "abc123"}


def _patch_bq_avg_mae(avg_mae: float | None) -> MagicMock:
    """Return a mock BQ module that yields a single row with avg_mae."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: avg_mae
    mock_client = MagicMock()
    mock_client.query.return_value.__iter__ = MagicMock(return_value=iter([row]))
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    return mock_bq


def _patch_bq_empty() -> MagicMock:
    """Return a mock BQ module that yields no rows."""
    mock_client = MagicMock()
    mock_client.query.return_value.__iter__ = MagicMock(return_value=iter([]))
    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client
    return mock_bq


# ---------------------------------------------------------------------------
# check_performance_alert
# ---------------------------------------------------------------------------


class TestCheckPerformanceAlert:
    def test_fires_when_mae_exceeds_threshold(self) -> None:
        # 2.0 × 1.25 = 2.5 > 2.0 × 1.20 = 2.40 → should fire
        mock_bq = _patch_bq_avg_mae(2.5)
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            patch("src.monitoring.alerts.get_prod_model_metrics", return_value=_PROD_METRICS),
        ):
            result = check_performance_alert(_PROJECT, _DATASET)
        assert result is True

    def test_clear_when_mae_ok(self) -> None:
        # 2.0 × 1.10 = 2.2 < 2.0 × 1.20 = 2.40 → should not fire
        mock_bq = _patch_bq_avg_mae(2.2)
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            patch("src.monitoring.alerts.get_prod_model_metrics", return_value=_PROD_METRICS),
        ):
            result = check_performance_alert(_PROJECT, _DATASET)
        assert result is False

    def test_false_when_no_bq_data(self) -> None:
        mock_bq = _patch_bq_empty()
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            patch("src.monitoring.alerts.get_prod_model_metrics", return_value=_PROD_METRICS),
        ):
            result = check_performance_alert(_PROJECT, _DATASET)
        assert result is False

    def test_false_when_avg_mae_is_none(self) -> None:
        mock_bq = _patch_bq_avg_mae(None)
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            patch("src.monitoring.alerts.get_prod_model_metrics", return_value=_PROD_METRICS),
        ):
            result = check_performance_alert(_PROJECT, _DATASET)
        assert result is False

    def test_false_when_no_prod_model(self) -> None:
        mock_bq = _patch_bq_avg_mae(3.0)
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            patch("src.monitoring.alerts.get_prod_model_metrics", return_value=None),
        ):
            result = check_performance_alert(_PROJECT, _DATASET)
        assert result is False

    def test_exactly_at_threshold_does_not_fire(self) -> None:
        # exactly 1.20 × training_mae → should NOT fire (strict >)
        mock_bq = _patch_bq_avg_mae(2.0 * 1.20)
        with (
            patch.dict("sys.modules", {"google.cloud.bigquery": mock_bq}),
            patch("src.monitoring.alerts.get_prod_model_metrics", return_value=_PROD_METRICS),
        ):
            result = check_performance_alert(_PROJECT, _DATASET)
        assert result is False


# ---------------------------------------------------------------------------
# check_drift_alert
# ---------------------------------------------------------------------------


class TestCheckDriftAlert:
    def test_fires_when_share_high(self) -> None:
        summary = {"share_drifted": 0.35, "drifted_feature_names": ["feat_a", "feat_b"]}
        assert check_drift_alert(summary) is True

    def test_clear_when_share_low(self) -> None:
        summary = {"share_drifted": 0.20, "drifted_feature_names": []}
        assert check_drift_alert(summary) is False

    def test_false_on_empty_summary(self) -> None:
        assert check_drift_alert({}) is False

    def test_exactly_at_threshold_does_not_fire(self) -> None:
        # exactly 0.30 → should NOT fire (strict >)
        summary = {"share_drifted": 0.30}
        assert check_drift_alert(summary) is False

    def test_fires_at_just_above_threshold(self) -> None:
        summary = {"share_drifted": 0.301}
        assert check_drift_alert(summary) is True
