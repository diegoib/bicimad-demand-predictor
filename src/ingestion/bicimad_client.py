"""BiciMAD API client.

Handles authentication (GET /v2/mobilitylabs/user/login/ with credentials as headers),
24-hour token caching, and station data fetching with exponential-backoff retries.

Credentials are never stored in Settings — they are read from environment variables
in dev (loaded from .env) or from Google Secret Manager in prod.
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
_DEFAULT_CACHE_PATH = Path("data/.token_cache.json")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def get_emt_credentials() -> tuple[str, str]:
    """Return (email, password) for the EMT MobilityLabs API.

    Dev:  reads BICIMAD_EMT_EMAIL / BICIMAD_EMT_PASSWORD from the environment.
          The .env file is loaded automatically by pydantic-settings when
          ``Settings`` is imported; if calling this before that happens, ensure
          the .env is sourced or the variables are exported.
    Prod: fetches secrets from Google Secret Manager using the project id set
          in BICIMAD_BQ_PROJECT.

    Returns:
        Tuple of (email, password).

    Raises:
        RuntimeError: If credentials cannot be found.
    """
    env = os.environ.get("BICIMAD_ENV", "dev")

    if env == "prod":
        return _credentials_from_secret_manager()

    # Dev — read from environment (populated from .env by pydantic-settings or shell)
    email = os.environ.get("BICIMAD_EMT_EMAIL", "")
    password = os.environ.get("BICIMAD_EMT_PASSWORD", "")

    if not email or not password:
        # Attempt an explicit .env load as a last resort, then retry
        _load_dotenv_if_available()
        email = os.environ.get("BICIMAD_EMT_EMAIL", "")
        password = os.environ.get("BICIMAD_EMT_PASSWORD", "")

    # Also accept unprefixed EMAIL / PASSWORD (common .env convention)
    if not email:
        email = os.environ.get("EMAIL", "")
    if not password:
        password = os.environ.get("PASSWORD", "")

    if not email or not password:
        raise RuntimeError(
            "EMT credentials not found. "
            "Set BICIMAD_EMT_EMAIL and BICIMAD_EMT_PASSWORD (or EMAIL and PASSWORD) "
            "in your .env file."
        )

    return email, password


def _load_dotenv_if_available() -> None:
    """Load .env into os.environ if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _credentials_from_secret_manager() -> tuple[str, str]:
    """Fetch EMT credentials from Google Secret Manager."""
    from google.cloud import secretmanager

    project_id = os.environ.get("BICIMAD_BQ_PROJECT", "")
    if not project_id:
        raise RuntimeError("BICIMAD_BQ_PROJECT must be set to use Secret Manager.")

    client = secretmanager.SecretManagerServiceClient()  # type: ignore[attr-defined]

    def _access(secret_id: str) -> str:
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return str(response.payload.data.decode("utf-8").strip())

    return _access("bicimad-emt-email"), _access("bicimad-emt-password")


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


class TokenCache:
    """Persist and validate the BiciMAD access token on local disk.

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
    """Return a valid access token, using the local cache when possible.

    In Cloud Function (prod) deployments pass ``cache=None`` to always
    re-authenticate (no persistent disk available).

    Args:
        cache: Optional TokenCache instance for local caching.

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
