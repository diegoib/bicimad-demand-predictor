"""BiciMAD API client.

Handles authentication (GET /v2/mobilitylabs/user/login/ with credentials as headers),
24-hour token caching, and station data fetching with exponential-backoff retries.

Credentials are always read from Google Secret Manager. Use Application Default
Credentials (gcloud auth application-default login) for local development with
a dev GCP project.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from src.common.logging_setup import get_logger
from src.common.schemas import BicimadApiResponse

logger = get_logger(__name__)

EMT_BASE_URL = "https://openapi.emtmadrid.es"
TOKEN_TTL_SECONDS = 23 * 3600  # 23 h — token expires in 24 h, use 23 h to be safe

# Configurable cache path — /tmp is guaranteed writable in any Linux environment
# (Airflow VM, Cloud Run, local dev). Override with BICIMAD_TOKEN_CACHE_PATH.
_DEFAULT_CACHE_PATH = Path(
    os.environ.get("BICIMAD_TOKEN_CACHE_PATH", "/tmp/.bicimad_token_cache.json")
)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def get_emt_credentials() -> tuple[str, str]:
    """Return (email, password) for the EMT MobilityLabs API from Secret Manager.

    Uses Application Default Credentials — run ``gcloud auth application-default
    login`` for local development against the dev GCP project.

    Returns:
        Tuple of (email, password).

    Raises:
        RuntimeError: If credentials cannot be found.
    """
    return _credentials_from_secret_manager()


def _credentials_from_secret_manager() -> tuple[str, str]:
    """Fetch EMT credentials from Google Secret Manager."""
    from google.cloud import secretmanager

    project_id = os.environ.get("BICIMAD_GCP_PROJECT", "")
    if not project_id:
        raise RuntimeError("BICIMAD_GCP_PROJECT must be set to use Secret Manager.")

    client = secretmanager.SecretManagerServiceClient()

    def _access(secret_id: str) -> str:
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return str(response.payload.data.decode("utf-8").strip())

    return _access("bicimad-emt-email"), _access("bicimad-emt-password")


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


class TokenCache:
    """Persist and validate the BiciMAD access token on disk.

    The Airflow worker runs on a persistent VM (e2-medium) — the cache file
    survives between DAG task invocations, avoiding a redundant login on each
    of the ~96 daily 15-minute ingestion cycles.

    The cache path defaults to ``/tmp/.bicimad_token_cache.json`` (guaranteed
    writable on any Linux host) and can be overridden via the
    ``BICIMAD_TOKEN_CACHE_PATH`` environment variable.

    Args:
        cache_path: Path to the JSON cache file.
    """

    def __init__(self, cache_path: Path = _DEFAULT_CACHE_PATH) -> None:
        self._cache_path = cache_path

    def load(self) -> str | None:
        """Return a cached token if it is still valid, otherwise None.

        Returns:
            The cached access token, or None if expired / missing.
        """
        if not self._cache_path.exists():
            return None
        try:
            data: dict[str, str] = json.loads(self._cache_path.read_text())
            issued_at = datetime.fromisoformat(data["issued_at"])
            age_seconds = (datetime.now(tz=UTC) - issued_at).total_seconds()
            if age_seconds < TOKEN_TTL_SECONDS:
                logger.debug("Loaded cached token (age %.0f s)", age_seconds)
                return data["token"]
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Token cache unreadable, will re-authenticate: %s", exc)
        return None

    def save(self, token: str) -> None:
        """Persist a newly obtained token with the current timestamp.

        Args:
            token: The access token string to cache.
        """
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"token": token, "issued_at": datetime.now(tz=UTC).isoformat()}
        self._cache_path.write_text(json.dumps(payload))
        logger.debug("Token cached at %s", self._cache_path)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def login() -> str:
    """Authenticate with EMT MobilityLabs and return a fresh access token.

    Sends credentials as HTTP headers to GET /v2/mobilitylabs/user/login/.
    Retries up to 3 times with exponential backoff on transient failures.

    Returns:
        The access token string.

    Raises:
        RuntimeError: After 3 failed attempts.
    """
    email, password = get_emt_credentials()
    url = f"{EMT_BASE_URL}/v2/mobilitylabs/user/login/"
    last_exc: Exception = RuntimeError("Login failed")

    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                headers={"email": email, "password": password},
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            # '00' = new token issued, '01' = existing token extended (both valid)
            if body.get("code") not in ("00", "01"):
                raise ValueError(
                    f"Login rejected by API: code={body.get('code')!r} "
                    f"description={body.get('description')!r}"
                )
            token: str = body["data"][0]["accessToken"]
            logger.info("BiciMAD login successful")
            return token
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                wait = 2**attempt
                logger.warning(
                    "Login attempt %d/3 failed: %s — retrying in %ds", attempt, exc, wait
                )
                time.sleep(wait)

    raise RuntimeError(f"BiciMAD login failed after 3 attempts: {last_exc}") from last_exc


def get_valid_token(cache: TokenCache | None = None) -> str:
    """Return a valid access token, using the disk cache when available.

    Args:
        cache: Optional TokenCache instance. Pass None to always re-authenticate
               (e.g. in stateless Cloud Function deployments).

    Returns:
        A valid access token string.
    """
    if cache is not None:
        cached = cache.load()
        if cached:
            return cached

    token = login()

    if cache is not None:
        cache.save(token)

    return token


# ---------------------------------------------------------------------------
# Stations endpoint
# ---------------------------------------------------------------------------


def fetch_stations(access_token: str) -> BicimadApiResponse:
    """Fetch the current state of all BiciMAD stations.

    Args:
        access_token: A valid EMT access token.

    Returns:
        Parsed and validated BicimadApiResponse.

    Raises:
        RuntimeError: After 3 failed attempts.
    """
    url = f"{EMT_BASE_URL}/v2/transport/bicimad/stations/"
    last_exc: Exception = RuntimeError("fetch_stations failed")

    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                headers={"accessToken": access_token},
                timeout=15,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != "00":
                raise ValueError(
                    f"Stations API error: code={body.get('code')!r} "
                    f"description={body.get('description')!r}"
                )
            parsed = BicimadApiResponse.model_validate(body)
            logger.info("Fetched %d stations from BiciMAD", len(parsed.data))
            return parsed
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                wait = 2**attempt
                logger.warning(
                    "fetch_stations attempt %d/3 failed: %s — retrying in %ds", attempt, exc, wait
                )
                time.sleep(wait)

    raise RuntimeError(f"fetch_stations failed after 3 attempts: {last_exc}") from last_exc
