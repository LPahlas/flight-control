"""Black-box concurrency checks using only the public HTTP API and stdlib."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

TIMING_RE = re.compile(r"(?:^|[,;]\s*)dur=([0-9]+(?:\.[0-9]+)?)")


class CheckFailed(Exception):
    pass


def call(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    started = time.perf_counter()
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as exc:
        response = exc
    except URLError as exc:
        raise CheckFailed(f"{method} {path}: {exc.reason}") from exc

    with response:
        elapsed_ms = (time.perf_counter() - started) * 1000
        timing = response.headers.get("Server-Timing", "")
        match = TIMING_RE.search(timing)
        if match is None:
            raise CheckFailed(f"{method} {path}: missing or invalid Server-Timing")
        server_ms = float(match.group(1))
        if not math.isfinite(server_ms) or server_ms > elapsed_ms + 1:
            raise CheckFailed(f"{method} {path}: invalid Server-Timing duration")
        try:
            body = json.loads(response.read())
        except (ValueError, UnicodeDecodeError) as exc:
            raise CheckFailed(f"{method} {path}: response was not JSON") from exc
        if not isinstance(body, dict):
            raise CheckFailed(f"{method} {path}: expected a JSON object")
        return response.status, body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def reset(base_url: str) -> None:
    status, body = call(base_url, "/admin/reset", method="POST")
    require(status == 200 and body.get("status") == "reset", "admin reset failed")


def choose_empty_small_flight(base_url: str) -> str:
    query = urlencode(
        {
            "origin": "BOS",
            "destination": "JFK",
            "date": "2027-05-04",
            "passengers": 9,
        }
    )
    status, result = call(base_url, f"/search?{query}")
    require(status == 200, "search did not return 200")
    itineraries = result.get("itineraries")
    require(isinstance(itineraries, list), "search response has no itineraries list")

    for itinerary in itineraries:
        if not isinstance(itinerary, dict) or itinerary.get("stops") != 0:
            continue
        flight_ids = itinerary.get("flight_ids")
        if not isinstance(flight_ids, list) or len(flight_ids) != 1:
            continue
        flight_id = flight_ids[0]
        status, counts = call(base_url, f"/admin/flights/{flight_id}")
        if (
            status == 200
            and counts.get("capacity") == 10
            and counts.get("seats_available") == 10
        ):
            return flight_id
    raise CheckFailed("could not find an empty nonstop flight with capacity 10")


def run_checks(base_url: str) -> None:
    status, health = call(base_url, "/health")
    require(status == 200 and health.get("status") == "ok", "health check failed")
    require(isinstance(health.get("boot_id"), str), "health response has no boot_id")
    print("PASS: public health endpoint and Server-Timing")

    flight_id = choose_empty_small_flight(base_url)
    status, held = call(
        base_url,
        "/holds",
        method="POST",
        payload={"itinerary_id": flight_id, "passengers": 9},
    )
    require(status == 201, "could not reserve the first nine seats")

    def try_last_seat(_: int) -> tuple[int, dict]:
        return call(
            base_url,
            "/holds",
            method="POST",
            payload={"itinerary_id": flight_id, "passengers": 1},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
        hold_results = list(executor.map(try_last_seat, range(40)))
    statuses = [status for status, _ in hold_results]
    require(statuses.count(201) == 1, "last-seat race did not create exactly one hold")
    require(statuses.count(409) == 39, "last-seat race did not reject the other 39 holds")
    require(
        all(body.get("error") for status, body in hold_results if status == 409),
        "a losing hold did not return a contract error object",
    )

    status, counts = call(base_url, f"/admin/flights/{flight_id}")
    require(
        status == 200
        and counts.get("seats_held") == 10
        and counts.get("seats_available") == 0,
        "flight counts do not match the two concurrent holds",
    )
    print("PASS: 40 external concurrent holds produce exactly one last-seat winner")

    reset(base_url)
    flight_id = choose_empty_small_flight(base_url)
    status, hold = call(
        base_url,
        "/holds",
        method="POST",
        payload={"itinerary_id": flight_id, "passengers": 1},
    )
    require(status == 201, "could not create hold for booking retry check")
    booking_request = {
        "hold_id": hold["hold_id"],
        "passengers": [{"name": "Black Box Traveler"}],
        "payment_token": "tok_ok_blackbox",
        "idempotency_key": "blackbox-concurrent-booking",
    }

    def retry_booking(_: int) -> tuple[int, dict]:
        return call(base_url, "/bookings", method="POST", payload=booking_request)

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
        booking_results = list(executor.map(retry_booking, range(40)))
    first_status, original = booking_results[0]
    require(first_status == 201, "booking request did not return 201")
    require(
        all(status == first_status and body == original for status, body in booking_results),
        "duplicate booking requests did not replay one identical response",
    )

    changed_request = {**booking_request, "payment_token": "tok_ok_different"}
    status, error = call(
        base_url, "/bookings", method="POST", payload=changed_request
    )
    require(
        status == 422 and error.get("error"),
        "reusing an idempotency key with a different body was not rejected",
    )

    status, cancelled = call(
        base_url, f"/bookings/{original['booking_id']}", method="DELETE"
    )
    require(status == 200 and cancelled.get("status") == "cancelled", "cancel failed")
    status, replay = call(base_url, "/bookings", method="POST", payload=booking_request)
    require(
        status == 201 and replay == original,
        "booking retry did not replay its original response after cancellation",
    )
    status, stats = call(base_url, "/admin/stats")
    require(
        status == 200
        and stats.get("bookings_confirmed") == 0
        and stats.get("bookings_cancelled") == 1
        and stats.get("seats_sold") == 0,
        "booking stats disagree after cancellation",
    )
    status, counts = call(base_url, f"/admin/flights/{flight_id}")
    require(
        status == 200
        and counts.get("seats_sold") == 0
        and counts.get("seats_held") == 0
        and counts.get("seats_available") == counts.get("capacity"),
        "per-flight seat counts disagree after cancellation",
    )
    status, export = call(base_url, "/admin/bookings?offset=0&limit=500")
    exported = export.get("bookings")
    require(
        status == 200
        and isinstance(exported, list)
        and any(
            row.get("booking_id") == original["booking_id"]
            and row.get("status") == "cancelled"
            for row in exported
        ),
        "booking export does not contain the cancelled booking",
    )
    print("PASS: 40 external booking retries replay one response; cancel and audit agree")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise airline concurrency and idempotency over HTTP only."
    )
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="allow the verifier to clear all airline bookings and holds",
    )
    args = parser.parse_args()
    if not args.reset:
        parser.error("--reset is required; this check clears existing bookings and holds")
    if not args.url.startswith(("http://", "https://")):
        parser.error("--url must start with http:// or https://")

    try:
        reset(args.url)
        try:
            run_checks(args.url)
        finally:
            reset(args.url)
        print("PASS: test data cleared; existing catalog is unchanged")
        return 0
    except CheckFailed as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
