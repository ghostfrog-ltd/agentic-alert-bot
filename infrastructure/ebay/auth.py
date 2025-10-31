from __future__ import annotations

import base64
import os
import time
import requests
from typing import Optional

from infrastructure.utils.logger import get_logger
from infrastructure.db.schema import (
    load_cached_ebay_token,
    save_cached_ebay_token,
)
from infrastructure.utils.usage_tracker import increment_api_usage  # ✅ add this

logger = get_logger(__name__)

# process-level cache
_cached_token: str | None = None
_token_expiry: float = 0.0  # epoch seconds


class EbayAuthError(Exception):
    """Raised if we fail to obtain or refresh an eBay application token."""
    pass


class EbayAuth:
    """
    Centralised auth helper for GhostFrog.

    Responsibilities:
    - Load eBay app credentials from env
    - Fetch an OAuth application access token (client credentials grant)
    - Cache token + expiry in memory (per-process) AND in DB (cross-process)
    - Hand out a guaranteed-valid Bearer token to callers
    """

    def __init__(self):
        self.app_id = os.getenv("EBAY_APP_ID")          # aka Client ID
        self.cert_id = os.getenv("EBAY_CERT_ID")        # aka Client Secret
        self.api_base = os.getenv("EBAY_API_BASE", "").rstrip("/")
        self.scope = os.getenv(
            "EBAY_OAUTH_SCOPE",
            "https://api.ebay.com/oauth/api_scope"
        )

        if not self.app_id or not self.cert_id or not self.api_base:
            raise EbayAuthError(
                "Missing EBAY_APP_ID / EBAY_CERT_ID / EBAY_API_BASE in environment"
            )

        # instance cache
        self._token: Optional[str] = None
        self._token_expiry_ts: float = 0.0  # epoch seconds

    def _needs_refresh(self) -> bool:
        """
        Refresh if:
        - we don't have a token in this instance
        - OR expiry is within 60 seconds
        """
        now = time.time()
        return (
            self._token is None
            or now >= (self._token_expiry_ts - 60)
        )

    def _try_load_from_process_cache(self) -> bool:
        """
        Fast path: reuse globals set earlier in THIS Python process.
        Returns True if we populated self._token.
        """
        global _cached_token, _token_expiry

        if not _cached_token:
            return False

        now = time.time()
        # must have >5 minutes left to consider it reusable
        if now >= (_token_expiry - 300):
            return False

        self._token = _cached_token
        self._token_expiry_ts = _token_expiry
        logger.info("[EbayAuth] Using cached eBay token (process)")
        return True

    def _try_load_from_db_cache(self) -> bool:
        """
        Cross-process cache: read token/expiry from DB.
        Returns True if we populated self._token.
        """
        global _cached_token, _token_expiry

        row = load_cached_ebay_token()
        if not row:
            return False

        token, expiry_epoch = row
        now = time.time()

        # same freshness rule: >5 minutes left
        if now >= (expiry_epoch - 300):
            return False

        # hydrate instance
        self._token = token
        self._token_expiry_ts = expiry_epoch

        # hydrate process-level globals so future calls in this run are instant
        _cached_token = token
        _token_expiry = expiry_epoch

        logger.info("[EbayAuth] Using cached eBay token (DB)")
        return True

    def _fetch_new_token_from_ebay(self) -> None:
        """
        Actually call eBay to get a fresh OAuth token and persist it.
        """
        token_url = f"{self.api_base}/identity/v1/oauth2/token"

        basic_auth_raw = f"{self.app_id}:{self.cert_id}".encode("utf-8")
        basic_auth_b64 = base64.b64encode(basic_auth_raw).decode("utf-8")
        headers = {
            "Authorization": f"Basic {basic_auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": self.scope,
        }

        logger.info("[EbayAuth] Requesting new application token from eBay")
        resp = requests.post(token_url, headers=headers, data=data, timeout=10)

        if resp.status_code != 200:
            logger.error(
                "[EbayAuth] Token request failed %s: %s",
                resp.status_code,
                resp.text[:500],
            )
            raise EbayAuthError(f"Token request failed with HTTP {resp.status_code}")

        # ✅ At this point the call to eBay succeeded, so count it
        increment_api_usage("ebay")

        payload = resp.json()
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")

        if not access_token or not expires_in:
            logger.error("[EbayAuth] Unexpected token payload: %s", payload)
            raise EbayAuthError("Token payload missing access_token/expires_in")

        expiry_epoch = time.time() + int(expires_in)

        # update instance
        self._token = access_token
        self._token_expiry_ts = expiry_epoch

        # update process globals
        global _cached_token, _token_expiry
        _cached_token = access_token
        _token_expiry = expiry_epoch

        # persist to DB
        save_cached_ebay_token(access_token, expiry_epoch)

        logger.info(f"[EbayAuth] Got new token (expires in ~{expires_in}s)")

    def _ensure_token(self) -> None:
        """
        Populate self._token with something valid, in this order:
        1. in-process cache
        2. DB cache
        3. fresh from eBay
        """
        if self._try_load_from_process_cache():
            return
        if self._try_load_from_db_cache():
            return
        self._fetch_new_token_from_ebay()

    def get_token(self) -> str:
        """
        Public entry point.
        Returns a valid Bearer token string.
        Auto-refreshes if we're expired / near expiry.
        """
        if self._needs_refresh():
            self._ensure_token()
        return self._token  # type: ignore[return-value]


# singleton accessor
_auth_singleton: Optional[EbayAuth] = None

def get_auth() -> EbayAuth:
    global _auth_singleton
    if _auth_singleton is None:
        _auth_singleton = EbayAuth()
    return _auth_singleton
