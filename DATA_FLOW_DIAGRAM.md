# Airline Reservation System Data Flow

```mermaid
flowchart LR
    traveler["End user<br/>Gradio browser UI"]
    grader["Flight Control grader<br/>Test 1 through Test 5"]
    verifier["Host HTTP verifier<br/>Standard library only"]
    dashboard["Optional instructor dashboard<br/>web + engine"]

    subgraph app["Docker application container"]
        gradio["Gradio mount<br/>/ui"]
        fastapi["FastAPI routers<br/>search, holds, bookings, admin"]
        catalog["Catalog helper<br/>flights and itineraries"]
        locking["Transaction helpers<br/>sorted flight locks and seat availability"]
        ui["End-user callbacks<br/>search, hold, book, cancel"]
    end

    subgraph dbcontainer["Docker database container"]
        postgres[("PostgreSQL 17")]
        wal["Write-ahead log<br/>and database pages"]
    end

    volume[("Named volume<br/>airlinedata")]
    image["Docker restart policy<br/>airline process"]

    traveler -->|"Trip and booking actions"| gradio
    gradio -->|"HTTP callbacks; no direct database access"| ui
    ui -->|"Public API requests"| fastapi

    grader -->|"Search, hold, booking, cancel, reset, restart requests"| fastapi
    verifier -->|"Black-box HTTP checks and races"| fastapi
    dashboard -->|"Optional run control and result display"| grader

    fastapi -->|"Catalog lookup"| catalog
    catalog -->|"Flight and itinerary data"| fastapi
    fastapi -->|"Connection-pool queries and transactions"| locking
    locking -->|"Read and write SQL"| postgres
    postgres <-->|"Persists pages and transaction commits"| wal
    wal -->|"Crash-safe storage"| volume

    postgres -->|"Rows, availability, booking and hold state"| locking
    locking -->|"JSON responses"| fastapi
    fastapi -->|"HTTP status and response data"| gradio
    fastapi -->|"HTTP results"| grader
    fastapi -->|"HTTP results"| verifier
    fastapi -->|"Health and admin audit data"| dashboard

    image -.->|"Restarts airline process"| fastapi
    postgres -.->|"Remains running and retains data"| volume
```

## Data Stores

- **PostgreSQL `flights`** stores the seeded airline catalog, including capacity and scheduled-leg information.
- **PostgreSQL `holds`** stores temporary seat reservations and their absolute expiration times.
- **PostgreSQL `hold_flights`** connects each hold to every flight leg it reserves.
- **PostgreSQL `bookings`** stores confirmed and cancelled bookings and passenger information.
- **PostgreSQL `booking_flights`** connects confirmed or cancelled bookings to flight legs and passenger counts.
- **PostgreSQL `idempotency_keys`** stores request bodies and original responses for duplicate booking requests.
- **PostgreSQL `service_state`** records restart timing used during application recovery.
- **Docker `airlinedata` volume** keeps the PostgreSQL data files across application and container restarts.

## Important Concurrency and Test Flows

1. The end user or grader sends an HTTP request to a FastAPI endpoint.
2. Hold and booking operations obtain a PostgreSQL transaction and lock every involved flight row in sorted flight-ID order.
3. SQL aggregates active holds and confirmed bookings to calculate seats sold, seats held, and seats available.
4. The transaction commits the seat change and all of its associated rows together, or rolls the whole operation back.
5. Flight Control and the independent host verifier test through HTTP only. They do not import the airline implementation or read PostgreSQL directly.
6. The restart endpoint causes the airline process to exit; Docker restarts the process while PostgreSQL and the named data volume remain available.
