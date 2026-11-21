# Airline API

This is the PostgreSQL-backed reference airline service for the Flight Control
contract. It and Flight Control's grader/dashboard are separate services in
one repository and Compose stack. The API listens on `http://localhost:8080`;
**no API key is required** by the contract.

## Start it

From the repository root, run:

```powershell
docker compose up --build
```

Docker starts PostgreSQL and the API. The first start loads the 2027 catalog
from Flight Control's `catalog` helper into PostgreSQL; that may take a little
while. Wait for the `airline` container to become healthy. PostgreSQL data is
kept in the `airlinedata` named volume, so restarting the API does not erase
bookings.

Check that it is responding:

```powershell
curl.exe http://localhost:8080/health
```

Open the browser interface at **http://localhost:8080/ui/**. The Search and
book tab supports finding itineraries, creating holds, submitting bookings,
and entering passenger names, payment tokens, and idempotency keys. Manage
bookings supports lookup and cancellation. The Instructor / admin tab exposes
health, statistics, flight seat counts, booking export, reset, and restart.
The interface calls the same HTTP API as other clients; no separate account or
API key is required.

Run a grader test from the repository root in another terminal. This uses the
CLI in the Docker image, so `uv` does not need to be installed on your host:

```powershell
docker compose run --rm --no-deps --no-TTY airline flightcontrol test --url http://airline:8080 --test 3
```

Run tests 1 through 5 by changing `--test`. Stop the services with
`docker compose down`. This keeps the database volume. To intentionally remove
its data, use `docker compose down --volumes`.

For a small independent HTTP-only check from the host, use Python's standard
library; it does not import the airline or need `uv`:

```powershell
py -3 airline/verify_api.py --url http://localhost:8080 --reset
```

The verifier sends requests only to the API URL, including its 40-way hold and
booking races. `--reset` is required because the check clears existing holds
and bookings before and after running. Use it only against a disposable test
instance.

The local Compose configuration uses a development database password and binds
the API to loopback. Before using it outside a trusted local machine, set a
strong `AIRLINE_DB_PASSWORD` in the repository-root `.env` file and configure
network access and TLS termination appropriately. Do not expose PostgreSQL
publicly.

## API

The API implements `/health`, `/search`, `/holds`, booking retrieval and
cancellation, plus the `/admin/*` endpoints used by Flight Control. Request
and response formats, itinerary rules, and error codes are specified in
[`../CONTRACT.md`](../CONTRACT.md). It returns `Server-Timing` on API
responses.

The optional instructor dashboard is a different service in the same Compose
stack. Follow the repository's main README to enable its `dashboard` profile;
from inside that stack, the team URL is `http://airline:8080`.

## Concurrency and persistence

PostgreSQL stores the catalog, holds, bookings, passengers, and idempotency
responses. A transaction locks all flight rows for an itinerary in sorted
flight-ID order before it checks live holds and confirmed bookings. This
serializes competing seat changes without relying on process-local Python
locks. A hold or booking spanning two flights either commits for both legs or
for neither.

Seat counts are derived in SQL from confirmed booking legs and unexpired hold
legs, instead of maintaining independently mutable counter copies. PostgreSQL
uses `READ COMMITTED` transactions, row-level locks, a bounded lock wait and
statement timeout; `synchronous_commit` remains enabled for acknowledged
changes. The API uses a bounded connection pool. These settings bound stalled
transactions; they do not replace the row locks or transaction constraints
that prevent double booking.

Holds expire according to their persisted UTC expiration time, including
downtime. Repeated booking requests are serialized on a unique idempotency-key
row, and the original response is stored and replayed for at least 24 hours
(this implementation keeps it until an admin reset). The restart endpoint
exits the API process; Compose restarts it while PostgreSQL and its volume
remain available.
