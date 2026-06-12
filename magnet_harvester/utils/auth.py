"""API Key authentication dependency for sensitive endpoints."""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from magnet_harvester.config import settings


async def require_api_key(x_api_key: str | None = Header(None)) -> None:
    """Require X-API-Key header when API_KEY is configured.

    If API_KEY is empty, authentication is disabled (backward compatible).
    """
    key = settings.API_KEY.strip()
    if not key:
        return

    if not x_api_key or x_api_key.strip() != key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
