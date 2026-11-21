from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from ..db import fetch_one
from .common import get_pool

router = APIRouter()
BOOT_ID = uuid.uuid4().hex


@router.get("/health")
async def health(pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    async with pool.connection() as conn:
        await fetch_one(conn, "SELECT 1 AS healthy")
    return {"status": "ok", "boot_id": BOOT_ID, "version": "1.0"}
