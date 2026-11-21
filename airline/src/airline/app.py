from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.errors import DeadlockDetected, LockNotAvailable, QueryCanceled
from psycopg_pool import AsyncConnectionPool, PoolTimeout

import gradio as gr

from .api import admin, bookings, health, holds, search
from .api.common import ApiError, error_response
from .db import create_pool, fetch_one, initialize_database
from .ui import USER_CSS, build_ui

log = logging.getLogger("airline")
RESTART_DOWNTIME_S = 30


async def _wait_out_requested_restart(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        row = await fetch_one(
            conn,
            """
            SELECT GREATEST(
                0,
                EXTRACT(EPOCH FROM (value + (%s * INTERVAL '1 second') - clock_timestamp()))
            ) AS seconds
            FROM service_state
            WHERE key = 'restart_requested_at'
            """,
            (RESTART_DOWNTIME_S,),
        )
    if row is None:
        return
    await asyncio.sleep(float(row["seconds"]))
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM service_state WHERE key = 'restart_requested_at'"
        )


async def _expire_holds(pool: AsyncConnectionPool) -> None:
    while True:
        await asyncio.sleep(5)
        async with pool.connection() as conn:
            await conn.execute(
                """
                DELETE FROM holds
                WHERE status = 'active' AND expires_at <= clock_timestamp()
                """
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    pool = create_pool()
    await pool.open(wait=True)
    cleanup_task: asyncio.Task | None = None
    try:
        await initialize_database(pool)
        await _wait_out_requested_restart(pool)
        app.state.pool = pool
        cleanup_task = asyncio.create_task(_expire_holds(pool))
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        await pool.close()


app = FastAPI(
    title="Airline API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def server_timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    return response


@app.exception_handler(ApiError)
async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.detail)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    count = len(exc.errors())
    return error_response(
        422,
        "bad_request",
        f"request has {count} missing or invalid field(s)",
    )


@app.exception_handler(PoolTimeout)
async def handle_pool_timeout(_: Request, exc: PoolTimeout) -> JSONResponse:
    return error_response(
        503,
        "database_busy",
        f"database connection pool is busy ({type(exc).__name__})",
    )


async def _database_busy(_: Request, exc: Exception) -> JSONResponse:
    return error_response(
        503,
        "database_busy",
        f"database operation could not complete ({type(exc).__name__})",
    )


for _error_type in (LockNotAvailable, QueryCanceled, DeadlockDetected):
    app.add_exception_handler(_error_type, _database_busy)


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    log.error(
        "unhandled airline API error",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return error_response(500, "internal_error", "the request could not be completed")


app.include_router(health.router)
app.include_router(search.router)
app.include_router(holds.router)
app.include_router(bookings.router)
app.include_router(admin.router)
app = gr.mount_gradio_app(
    app,
    build_ui(),
    path="/ui",
    css=USER_CSS,
    footer_links=[],
)
