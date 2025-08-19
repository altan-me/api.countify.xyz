# Counter API

The Counter API is a simple Flask application designed to track counts associated with unique IDs. It provides endpoints to get the total count, increment by one, and increase by a specified value. This document outlines the setup and usage of the Counter API.

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

You need Python and Docker installed on your system. You can install Flask using pip:

```bash
pip install flask argon2-cffi
```

### Installing

Clone the repository to your local machine and navigate to the directory containing the Dockerfile.

### Build and Run with Docker

Build the image:

```bash
docker build -t countify .
```

Run the container (Linux/macOS):

```bash
docker run -d --name countify \
  -p 5000:5000 \
  -e SECRET_KEY="change-me" \
  -e ADMIN_PASSWORD="set-admin-pass" \
  -e SESSION_COOKIE_SECURE=1 \
  -e SESSION_COOKIE_SAMESITE=Strict \
  -e TRUSTED_PROXY_DEPTH=1 \
  -e ENABLE_HSTS=1 \
  -v $(pwd)/data:/data \
  countify
```

Run the container (Windows PowerShell):

```powershell
docker run -d --name countify `
  -p 5000:5000 `
  -e SECRET_KEY="change-me" `
  -e ADMIN_PASSWORD="set-admin-pass" `
  -e SESSION_COOKIE_SECURE=1 `
  -e SESSION_COOKIE_SAMESITE=Strict `
  -e TRUSTED_PROXY_DEPTH=1 `
  -e ENABLE_HSTS=1 `
  -v ${PWD}\data:/data `
  countify
```

Notes:
- App listens on port 5000.
- Data is persisted under `./data` on the host.
- On first run, if `ADMIN_PASSWORD` is not provided, a temporary password is printed to the container logs. For production, set `ADMIN_PASSWORD` explicitly and rotate after first login.
- Sessions last 2 days and refresh on activity. Set a stable `SECRET_KEY` for persistence across restarts.
 - When running behind Nginx (or another reverse proxy), `TRUSTED_PROXY_DEPTH=1` enables trusted client IP extraction for rate limiting. Ensure the proxy sets and sanitizes `X-Forwarded-For`/`X-Forwarded-Proto`.
 - Set `SESSION_COOKIE_SECURE=1` and terminate TLS at the proxy. Enable HSTS with `ENABLE_HSTS=1` only over HTTPS.

## API Usage

The Counter API provides the following endpoints:

### Get Total Count

- **Endpoint**: `GET /get-total/{id}`
- **Description**: Retrieves the current total count for the specified ID. If the ID does not exist, it initializes it with a total of 0.
- **Example**: [https://api.countify.xyz/get-total/testID](https://api.countify.xyz/get-total/testID)

### Increment Count by One

- **Endpoint**: `POST /increment/{id}`
- **Description**: Increments the total count associated with the specified ID by 1. If the ID does not exist, it initializes it with a total of 1.
- **Example**: [https://api.countify.xyz/increment/testID](https://api.countify.xyz/increment/testID)

### Increase Count by a Specified Value

- **Endpoint**: `POST /increase/{id}`
- **Description**: Increases the total count associated with the specified ID by the value provided in the JSON payload. If the ID does not exist, it initializes it with the specified total.
- **Payload**:
  ```json
  {
    "value": 50
  }
  ```
- **Example**: [https://api.countify.xyz/increase/testID](https://api.countify.xyz/increase/testID)

### Stats

- **Endpoint**: `GET /stats`
- **Description**: Returns global statistics including `total_counters`, `total_count`, `last_activity`, `top_counters` and `recently_updated`.

## UI

- Landing page: `/`
- Dashboard: `/dashboard` (polls the `/stats` endpoint)

If running locally without Docker:

```bash
export DATABASE=./counters.db
export SECRET_KEY=change-me
export ADMIN_PASSWORD=set-admin-pass
export SESSION_COOKIE_SECURE=0
export SESSION_COOKIE_SAMESITE=Strict
export TRUSTED_PROXY_DEPTH=0
python app.py
```

## Testing

Run the automated tests to validate the API endpoints using pytest. The tests use Flask's test client and a temporary SQLite database, so you don't need to start the server.

### Windows (PowerShell)

```powershell
cd C:\Users\admin\Documents\GitHub\api.countify.xyz
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -U pip pytest flask argon2-cffi
pytest -q
```

### macOS/Linux

```bash
cd /path/to/api.countify.xyz
python -m venv .venv
source .venv/bin/activate
pip install -U pip pytest flask argon2-cffi
pytest -q
```

You can also run the single test file directly:

```bash
python tests/test_api.py
```

## Contributing

Please read [CONTRIBUTING.md](#) for details on our code of conduct, and the process for submitting pull requests to us.

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.

---
