"""Tests for src/ingestion/bicimad_client.py."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.bicimad_client import (
    TokenCache,
    fetch_stations,
    get_valid_token,
    login,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_LOGIN_RESPONSE = {
    "code": "00",
    "description": "OK",
    "datetime": "2025-06-15T14:00:00",
    "data": [{"accessToken": "test-token-abc123"}],
}

SAMPLE_STATIONS_RESPONSE = {
    "code": "00",
    "description": "OK",
    "datetime": "2025-06-15T14:00:00",
    "data": [
        {
            "id": 1,
            "number": "001",
            "name": "Puerta del Sol",
            "activate": 1,
            "no_available": 0,
            "total_bases": 24,
            "dock_bikes": 8,
            "free_bases": 16,
            "geometry": {"type": "Point", "coordinates": [-3.7038, 40.4168]},
        },
        {
            "id": 2,
            "number": "002",
            "name": "Gran Vía",
            "activate": 1,
            "no_available": 0,
            "total_bases": 20,
            "dock_bikes": 5,
            "free_bases": 15,
            "geometry": {"type": "Point", "coordinates": [-3.7028, 40.4200]},
        },
    ],
}


# ---------------------------------------------------------------------------
# TokenCache tests
# ---------------------------------------------------------------------------


class TestTokenCache:
    def test_load_returns_none_when_no_file(self, tmp_path: Path) -> None:
        cache = TokenCache(tmp_path / ".token_cache.json")
        assert cache.load() is None

    def test_save_and_load_valid_token(self, tmp_path: Path) -> None:
        cache = TokenCache(tmp_path / ".token_cache.json")
        cache.save("my-token")
        assert cache.load() == "my-token"

    def test_load_returns_none_for_expired_token(self, tmp_path: Path) -> None:
        cache_path = tmp_path / ".token_cache.json"
        # Write a cache entry that is 24+ hours old
        old_issued_at = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat()
        cache_path.write_text(json.dumps({"token": "old-token", "issued_at": old_issued_at}))
        cache = TokenCache(cache_path)
        assert cache.load() is None

    def test_load_returns_none_for_corrupt_file(self, tmp_path: Path) -> None:
        cache_path = tmp_path / ".token_cache.json"
        cache_path.write_text("not valid json {{{")
        cache = TokenCache(cache_path)
        assert cache.load() is None

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        cache = TokenCache(tmp_path / "sub" / "dir" / ".token_cache.json")
        cache.save("tok")
        assert cache.load() == "tok"


# ---------------------------------------------------------------------------
# login() tests
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_LOGIN_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with (
            patch("src.ingestion.bicimad_client.requests.get", return_value=mock_resp),
            patch(
                "src.ingestion.bicimad_client.get_emt_credentials",
                return_value=("user@test.com", "pass"),
            ),
        ):
            token = login()

        assert token == "test-token-abc123"

    def test_login_api_error_code_raises(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": "01", "description": "Invalid credentials"}
        mock_resp.raise_for_status.return_value = None

        with (
            patch("src.ingestion.bicimad_client.requests.get", return_value=mock_resp),
            patch(
                "src.ingestion.bicimad_client.get_emt_credentials",
                return_value=("user@test.com", "pass"),
            ),
            patch("src.ingestion.bicimad_client.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                login()

    def test_login_retries_on_http_error(self) -> None:
        import requests as req

        fail = MagicMock()
        fail.raise_for_status.side_effect = req.HTTPError("500 Server Error")

        success = MagicMock()
        success.json.return_value = SAMPLE_LOGIN_RESPONSE
        success.raise_for_status.return_value = None

        with (
            patch(
                "src.ingestion.bicimad_client.requests.get",
                side_effect=[fail, fail, success],
            ),
            patch(
                "src.ingestion.bicimad_client.get_emt_credentials",
                return_value=("u", "p"),
            ),
            patch("src.ingestion.bicimad_client.time.sleep"),
        ):
            token = login()

        assert token == "test-token-abc123"


# ---------------------------------------------------------------------------
# get_valid_token() tests
# ---------------------------------------------------------------------------


class TestGetValidToken:
    def test_uses_cache_when_valid(self, tmp_path: Path) -> None:
        cache = TokenCache(tmp_path / ".token_cache.json")
        cache.save("cached-token")

        with patch("src.ingestion.bicimad_client.login") as mock_login:
            result = get_valid_token(cache)

        assert result == "cached-token"
        mock_login.assert_not_called()

    def test_calls_login_when_cache_empty(self, tmp_path: Path) -> None:
        cache = TokenCache(tmp_path / ".token_cache.json")

        with patch("src.ingestion.bicimad_client.login", return_value="fresh-token") as mock_login:
            result = get_valid_token(cache)

        assert result == "fresh-token"
        mock_login.assert_called_once()

    def test_no_cache_always_calls_login(self) -> None:
        with patch("src.ingestion.bicimad_client.login", return_value="tok") as mock_login:
            result = get_valid_token(cache=None)

        assert result == "tok"
        mock_login.assert_called_once()

    def test_saves_new_token_to_cache(self, tmp_path: Path) -> None:
        cache = TokenCache(tmp_path / ".token_cache.json")

        with patch("src.ingestion.bicimad_client.login", return_value="new-token"):
            get_valid_token(cache)

        # Token should now be persisted
        assert cache.load() == "new-token"


# ---------------------------------------------------------------------------
# fetch_stations() tests
# ---------------------------------------------------------------------------


class TestFetchStations:
    def test_fetch_stations_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_STATIONS_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("src.ingestion.bicimad_client.requests.get", return_value=mock_resp):
            result = fetch_stations("my-token")

        assert result.code == "00"
        assert len(result.data) == 2
        assert result.data[0].name == "Puerta del Sol"
        assert result.data[0].dock_bikes == 8

    def test_fetch_stations_passes_token_as_header(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_STATIONS_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("src.ingestion.bicimad_client.requests.get", return_value=mock_resp) as mock_get:
            fetch_stations("tok-xyz")

        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["headers"]["accessToken"] == "tok-xyz"

    def test_fetch_stations_api_error_raises(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": "02", "description": "Service unavailable"}
        mock_resp.raise_for_status.return_value = None

        with (
            patch("src.ingestion.bicimad_client.requests.get", return_value=mock_resp),
            patch("src.ingestion.bicimad_client.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                fetch_stations("tok")

    def test_fetch_stations_validates_schema(self) -> None:
        """All returned stations must be valid StationSnapshot instances."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_STATIONS_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("src.ingestion.bicimad_client.requests.get", return_value=mock_resp):
            result = fetch_stations("tok")

        for station in result.data:
            assert station.total_bases >= station.dock_bikes + station.free_bases - 1
