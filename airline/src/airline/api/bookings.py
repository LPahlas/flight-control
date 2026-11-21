from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from psycopg_pool import AsyncConnectionPool

from ..db import fetch_one, jsonb
from ..seats import lock_flights
from .common import ApiError, booking_payload, get_pool

router = APIRouter()


class Passenger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hold_id: str = Field(min_length=1, max_length=100)
    passengers: list[Passenger] = Field(min_length=1, max_length=9)
    payment_token: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


async def _store_reply(conn, key: str, status: int, body: dict) -> None:
    await conn.execute(
        """
        UPDATE idempotency_keys
        SET response_status = %s, response_body = %s
        WHERE idempotency_key = %s
        """,
        (status, jsonb(body), key),
    )


@router.post("/bookings")
async def create_booking(
    request: BookingRequest,
    pool: AsyncConnectionPool = Depends(get_pool),
):
    request_body = request.model_dump(mode="json")
    passengers = request_body["passengers"]
    status_code: int
    response_body: dict

    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO idempotency_keys (idempotency_key, request_body)
                VALUES (%s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (request.idempotency_key, jsonb(request_body)),
            )
            record = await fetch_one(
                conn,
                """
                SELECT request_body, response_status, response_body
                FROM idempotency_keys
                WHERE idempotency_key = %s
                FOR UPDATE
                """,
                (request.idempotency_key,),
            )
            if record["request_body"] != request_body:
                raise ApiError(
                    422,
                    "idempotency_key_reused",
                    "the idempotency key was already used with a different request",
                )
            if record["response_status"] is not None:
                return _json_response(
                    int(record["response_status"]), record["response_body"]
                )

            hold = await fetch_one(
                conn,
                "SELECT * FROM holds WHERE hold_id = %s FOR UPDATE",
                (request.hold_id,),
            )
            if hold is None or hold["status"] != "active":
                status_code, response_body = 410, {
                    "error": "hold_expired",
                    "detail": "hold is unknown, expired, or already used",
                }
            else:
                alive = await fetch_one(
                    conn,
                    "SELECT %s::timestamptz > clock_timestamp() AS alive",
                    (hold["expires_at"],),
                )
                if not alive["alive"]:
                    await conn.execute(
                        "UPDATE holds SET status = 'expired' WHERE hold_id = %s",
                        (request.hold_id,),
                    )
                    status_code, response_body = 410, {
                        "error": "hold_expired",
                        "detail": "hold is unknown, expired, or already used",
                    }
                elif len(passengers) != hold["passengers"]:
                    status_code, response_body = 422, {
                        "error": "passenger_mismatch",
                        "detail": "passenger count must match the hold",
                    }
                else:
                    flight_ids = list(hold["flight_ids"])
                    locked = await lock_flights(conn, flight_ids)
                    if len(locked) != len(flight_ids):
                        raise RuntimeError("a held flight is missing from the catalog")

                    alive = await fetch_one(
                        conn,
                        "SELECT %s::timestamptz > clock_timestamp() AS alive",
                        (hold["expires_at"],),
                    )
                    if not alive["alive"]:
                        await conn.execute(
                            "UPDATE holds SET status = 'expired' WHERE hold_id = %s",
                            (request.hold_id,),
                        )
                        status_code, response_body = 410, {
                            "error": "hold_expired",
                            "detail": "hold is unknown, expired, or already used",
                        }
                    elif request.payment_token.startswith("tok_decline_"):
                        await conn.execute(
                            "UPDATE holds SET status = 'declined' WHERE hold_id = %s",
                            (request.hold_id,),
                        )
                        status_code, response_body = 402, {
                            "error": "payment_declined",
                            "detail": "payment was declined",
                        }
                    elif not request.payment_token.startswith("tok_ok_"):
                        await conn.execute(
                            "UPDATE holds SET status = 'declined' WHERE hold_id = %s",
                            (request.hold_id,),
                        )
                        status_code, response_body = 402, {
                            "error": "payment_declined",
                            "detail": "payment token is not accepted",
                        }
                    else:
                        booking_id = f"b_{uuid.uuid4().hex}"
                        row = await fetch_one(
                            conn,
                            """
                            INSERT INTO bookings (
                                booking_id, status, itinerary_id, flight_ids, passengers,
                                passenger_count, total_cents
                            )
                            VALUES (%s, 'confirmed', %s, %s, %s, %s, %s)
                            RETURNING *
                            """,
                            (
                                booking_id,
                                hold["itinerary_id"],
                                flight_ids,
                                jsonb(passengers),
                                hold["passengers"],
                                hold["total_cents"],
                            ),
                        )
                        for order, flight_id in enumerate(flight_ids):
                            await conn.execute(
                                """
                                INSERT INTO booking_flights (
                                    booking_id, flight_id, leg_order, passengers
                                )
                                VALUES (%s, %s, %s, %s)
                                """,
                                (booking_id, flight_id, order, hold["passengers"]),
                            )
                        await conn.execute(
                            "UPDATE holds SET status = 'booked' WHERE hold_id = %s",
                            (request.hold_id,),
                        )
                        status_code, response_body = 201, booking_payload(row)

            await _store_reply(
                conn, request.idempotency_key, status_code, response_body
            )

    return _json_response(status_code, response_body)


def _json_response(status_code: int, body: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=body)


@router.get("/bookings/{booking_id}")
async def get_booking(
    booking_id: str,
    pool: AsyncConnectionPool = Depends(get_pool),
):
    async with pool.connection() as conn:
        row = await fetch_one(
            conn,
            "SELECT * FROM bookings WHERE booking_id = %s",
            (booking_id,),
        )
    if row is None:
        raise ApiError(404, "not_found", "booking not found")
    return booking_payload(row)


@router.delete("/bookings/{booking_id}")
async def cancel_booking(
    booking_id: str,
    pool: AsyncConnectionPool = Depends(get_pool),
):
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await fetch_one(
                conn,
                "SELECT * FROM bookings WHERE booking_id = %s FOR UPDATE",
                (booking_id,),
            )
            if row is None:
                raise ApiError(404, "not_found", "booking not found")
            if row["status"] == "confirmed":
                flight_ids = list(row["flight_ids"])
                locked = await lock_flights(conn, flight_ids)
                if len(locked) != len(flight_ids):
                    raise RuntimeError("a booked flight is missing from the catalog")
                row = await fetch_one(
                    conn,
                    """
                    UPDATE bookings
                    SET status = 'cancelled'
                    WHERE booking_id = %s
                    RETURNING *
                    """,
                    (booking_id,),
                )
    return booking_payload(row)
