# Flight Control

Flight Control is the traffic generator and grader for Projects 2 and 3 in
COMP 4340/6340 (Advanced Database Systems) at Northwest Nazarene University.

Each team builds an airline booking service that follows
[CONTRACT.md](CONTRACT.md) and runs it on their own machine. Flight Control
connects to it at `localhost` and plays the part of thousands of travelers.
It searches, holds seats, books, cancels and audits, and it checks every
answer against its own record of what happened.

## For students

Read [CONTRACT.md](CONTRACT.md) first. It is the whole specification. How you
build the airline behind it is up to you.

Start with the Getting started section of the contract. It runs a small toy
airline from `examples/` and shows Flight Control calling it, one line per
call, so you can see the whole loop work before you write anything.

Run every test on your own machine against your airline on `localhost`.
Flight Control's command-line grader needs no public URL, tunnel, Docker
Compose setup, `.env`, or admin password.

```
git clone https://github.com/enochlev/flight-control
cd flight-control
uv sync
uv run flightcontrol test --url http://localhost:8080 --test 1 --verbose
```

`--test` takes 1 to 5. Use `--seed` to run a test with a different seed.

## What is in this repository

| Path | What it is |
|---|---|
| `CONTRACT.md` | the API every airline serves, and how to get started |
| `examples/toy_airline.py` | a demo airline with no database, for seeing the loop work |
| `src/flightcontrol/catalog.py` | builds the flight catalog from a fixed seed |
| `src/flightcontrol/oracle.py` | the rules for a valid itinerary |
| `src/flightcontrol/workload.py` | travelers, and the checks on every answer |
| `src/flightcontrol/grading.py` | tests 1 to 5 |
| `src/flightcontrol/engine.py` | the process that sends all traffic |
| `src/flightcontrol/web.py` | the web site |
| `airline/` | the PostgreSQL-backed reference airline API; see [airline/README.md](airline/README.md) |

## Run the reference airline

The reference airline and Flight Control are separate services in the same
repository and Compose stack. From the repository root, run:

```
docker compose up --build
```

The airline API listens on `http://localhost:8080`; it requires no API key.
The first startup loads the 2027 catalog into PostgreSQL and can take a little
while. PostgreSQL data is kept in a named volume. Stop the services with
`docker compose down`; remove the data only with `docker compose down --volumes`.
See [airline/README.md](airline/README.md) for the API design and test details.

## Optional instructor dashboard

The dashboard is separate from the command-line grader. To try it locally,
run `uv run flightcontrol web` and `uv run flightcontrol engine` in separate
terminals from the repository root. Set `FC_DEV=1` in both terminals and set
`FC_ADMIN_PASSWORD` in the web terminal. Open
`http://127.0.0.1:8000/admin` and sign in as `admin`. Create a team, save
your airline's localhost URL on the team page, and approve it in the dashboard.
Use `FC_DEV=1` only for a trusted local dashboard.

For the local dashboard in Compose, copy `.env.example` to `.env`, set
`FC_ADMIN_PASSWORD`, and set `FC_DEV=1`. Open the team page and set the airline
URL to `http://airline:8080`, then approve that URL. Use `FC_DEV=1` only for a
trusted local dashboard.

```bash
docker compose --profile dashboard up -d --build
```

The dashboard publishes at `127.0.0.1:8000`; the airline API remains at
`127.0.0.1:8080`. For a hosted dashboard, set `FC_DEV=0`, configure
`FC_PUBLIC_URL` and `FC_ALLOWED_HOST_SUFFIXES`, and serve it over HTTPS.
Hosted Flight Control runs only call an instructor-approved HTTPS URL on an
explicitly allowed domain that resolves to a public address. Flight Control
never follows redirects.
