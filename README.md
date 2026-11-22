# Flight Control

Follow these steps to download Flight Control, start its reference airline
and PostgreSQL database, and run the tests. The commands below are for
Windows PowerShell.

## 1. Install the prerequisites

Install:

- [Git for Windows](https://git-scm.com/download/win)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start
  Docker Desktop before continuing
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Python 3.12 or newer

## 2. Clone the repository from GitHub

On the Flight Control GitHub repository page, select **Code**, choose **HTTPS**,
and copy the repository URL. Open a PowerShell terminal and run the following
commands. They clone the repository into your Documents folder:

```powershell
Set-Location "$HOME\Documents"
git clone https://github.com/LPahlas/flight-control.git
Set-Location "$HOME\Documents\flight-control"
uv sync
```

`uv sync` installs Flight Control and its dependencies. The last command also
sets the current directory to the repository root, which contains
`pyproject.toml`. Run the remaining `uv` and `docker compose` commands from
this directory. If you cloned the repository somewhere else, replace
`$HOME\Documents\flight-control` in the commands below with your clone's
location.

## 3. Start the airline and database

Keep the first PowerShell terminal open. From the repository root, start the
services:

```powershell
docker compose up --build
```

This starts the PostgreSQL database and reference airline API. On its first
start, the application creates the database schema and loads the 2027 flight
catalog; this can take a little while. Wait until the `airline` service is
healthy. PostgreSQL data is kept in a Docker volume, so it remains when the
services stop and start again.

## 4. Open another terminal and check the service

Leave the first terminal running. Open a **second PowerShell terminal** and
change its directory to the repository root:

```powershell
Set-Location "$HOME\Documents\flight-control"
docker compose ps
```

Check that the airline API responds:

```powershell
curl.exe http://localhost:8080/health
```

Wait for the `airline` service status to show **healthy** and for the health
request to return a response before continuing.

The API does not require an API key. The airline's traveler website is at
[http://localhost:8080/ui/](http://localhost:8080/ui/). You can search for
flights, hold seats, confirm a booking with a demo payment, and look up or
cancel a booking using its reference.

## 5. Run the project test suite

In the second terminal, still at the repository root, run the automated
project tests:

```powershell
uv run pytest
```

## 6. Run all five Flight Control grader tests

Run these commands **in order, one at a time, in the second terminal**. Wait
for each test to finish before starting the next. Each command tests the
airline at `http://localhost:8080`.

**Test 1 — steady traffic** (runs for two minutes by default):

```powershell
uv run flightcontrol test --url http://localhost:8080 --test 1
```

**Test 2 — rush traffic:**

```powershell
uv run flightcontrol test --url http://localhost:8080 --test 2
```

**Test 3 — last-seat concurrency:**

```powershell
uv run flightcontrol test --url http://localhost:8080 --test 3
```

**Test 4 — restart and persistence:**

```powershell
uv run flightcontrol test --url http://localhost:8080 --test 4
```

**Test 5 — booking audit:**

```powershell
uv run flightcontrol test --url http://localhost:8080 --test 5
```

Tests 2–5 clear the airline's holds and bookings before running. Run the
grader tests against a development database whose data can be reset. Test 4
restarts the airline; Docker Compose starts it again automatically. Tests can
take several minutes to finish.



## Stop the services

When finished, return to the first terminal and press **Ctrl+C** to stop the
foreground Compose services. To stop and remove the containers while keeping
the database volume, open a terminal at the repository root and run:

```powershell
docker compose down
```

To intentionally delete the database and all its saved data as well, run
`docker compose down --volumes` instead.

For the API contract and more information about the reference airline, see
[CONTRACT.md](CONTRACT.md) and [airline/README.md](airline/README.md).
