from __future__ import annotations

from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from flightcontrol import catalog


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def error_response(status_code: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse(
        {"error": code, "detail": detail},
        status_code=status_code,
    )


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


def booking_payload(row: dict) -> dict:
    created_at: datetime = row["created_at"]
    return {
        "booking_id": row["booking_id"],
        "status": row["status"],
        "itinerary_id": row["itinerary_id"],
        "flight_ids": list(row["flight_ids"]),
        "passengers": row["passengers"],
        "total_cents": int(row["total_cents"]),
        "created_at": catalog.iso(created_at),
    }
