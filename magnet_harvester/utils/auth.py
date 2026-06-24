"""API Key authentication dependency for sensitive endpoints."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, status


async def require_api_key(request: Request, x_api_key: str | None = Header(None)) -> None:
    """Require X-API-Key header when API_KEY is configured.

    If API_KEY is empty, authentication is disabled (backward compatible).
    """
    ctx = request.app.state.ctx
    key = ctx.api_key.strip() if ctx.api_key else ""
    if not key:
        return

    if not x_api_key or not secrets.compare_digest(x_api_key.strip(), key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
