"""Static page delivery endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@router.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
