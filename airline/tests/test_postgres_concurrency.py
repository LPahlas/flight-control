from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airline import db  # noqa: E402
from airline.app import app  # noqa: E402
from flightcontrol import catalog  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AIRLINE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set AIRLINE_TEST_DATABASE_URL to a disposable PostgreSQL database",
)


async def client_lifecycle(run):
    db.DATABASE_URL = TEST_DATABASE_URL
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://airline.test",
        ) as client:
            await client.post("/admin/reset")
            try:
                await run(client)
            finally:
                await client.post("/admin/reset")


def last_seat_race() -> None:
    flight = next(f for f in catalog.get().flights if f.capacity == 10)

    async def run(client: httpx.AsyncClient) -> None:
        itinerary = flight.flight_id
        initial = await client.post(
            "/holds",
            json={"itinerary_id": itinerary, "passengers": 9},
        )
        assert initial.status_code == 201

        attempts = await asyncio.gather(
            *(
                client.post(
                    "/holds",
                    json={"itinerary_id": itinerary, "passengers": 1},
                )
                for _ in range(40)
            )
        )
        assert sum(response.status_code == 201 for response in attempts) == 1
        assert sum(response.status_code == 409 for response in attempts) == 39

        counts = await client.get(f"/admin/flights/{itinerary}")
        assert counts.status_code == 200
        assert counts.json()["seats_held"] == 10
        assert counts.json()["seats_available"] == 0

    asyncio.run(client_lifecycle(run))


def duplicate_booking_race() -> None:
    flight = next(f for f in catalog.get().flights if f.capacity == 10)

    async def run(client: httpx.AsyncClient) -> None:
        hold = await client.post(
            "/holds",
            json={"itinerary_id": flight.flight_id, "passengers": 1},
        )
        assert hold.status_code == 201
        body = {
            "hold_id": hold.json()["hold_id"],
            "passengers": [{"name": "Concurrent Traveler"}],
            "payment_token": "tok_ok_concurrency",
            "idempotency_key": "concurrent-booking-key",
        }

        responses = await asyncio.gather(
            *(client.post("/bookings", json=body) for _ in range(40))
        )
        assert {response.status_code for response in responses} == {201}
        assert all(response.json() == responses[0].json() for response in responses)

        stats = await client.get("/admin/stats")
        assert stats.json()["bookings_confirmed"] == 1
        assert stats.json()["seats_sold"] == 1

    asyncio.run(client_lifecycle(run))


def idempotency_survives_cancellation() -> None:
    flight = next(f for f in catalog.get().flights if f.capacity == 10)

    async def run(client: httpx.AsyncClient) -> None:
        hold = await client.post(
            "/holds",
            json={"itinerary_id": flight.flight_id, "passengers": 1},
        )
        assert hold.status_code == 201
        body = {
            "hold_id": hold.json()["hold_id"],
            "passengers": [{"name": "Replay Traveler"}],
            "payment_token": "tok_ok_replay",
            "idempotency_key": "replay-after-cancel",
        }
        first = await client.post("/bookings", json=body)
        assert first.status_code == 201

        cancelled = await client.delete(
            f"/bookings/{first.json()['booking_id']}"
        )
        assert cancelled.json()["status"] == "cancelled"
        replay = await client.post("/bookings", json=body)
        assert replay.status_code == 201
        assert replay.json() == first.json()

        changed_body = {**body, "payment_token": "tok_ok_different"}
        changed = await client.post("/bookings", json=changed_body)
        assert changed.status_code == 422

        stats = await client.get("/admin/stats")
        assert stats.json()["bookings_confirmed"] == 0
        assert stats.json()["bookings_cancelled"] == 1
        assert stats.json()["seats_sold"] == 0

    asyncio.run(client_lifecycle(run))


def test_concurrent_holds_do_not_double_book_last_seat() -> None:
    last_seat_race()


def test_concurrent_booking_retries_replay_one_booking() -> None:
    duplicate_booking_race()


def test_idempotency_replays_original_answer_after_cancel() -> None:
    idempotency_survives_cancellation()
