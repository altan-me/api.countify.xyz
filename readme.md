# Counter API

A secure Flask application for tracking counts associated with unique IDs. Features both public counters and user-specific counters with authentication, a web dashboard, and comprehensive security measures. Supports both API endpoints and a web interface for counter management.

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

### Docker Configuration Notes

- App listens on port 5000
- Uses Gunicorn WSGI server with 4 workers and threading for production deployment
- Data is persisted under `./data` on the host
- On first run, if `ADMIN_PASSWORD` is not provided, a temporary password is printed to the container logs
- For production, set `ADMIN_PASSWORD` explicitly and change it after first login
- Sessions last 2 days and refresh on activity - set a stable `SECRET_KEY` for persistence across restarts
- When running behind a reverse proxy, set `TRUSTED_PROXY_DEPTH=1` for proper client IP detection
- For HTTPS deployments, set `SESSION_COOKIE_SECURE=1` and `ENABLE_HSTS=1`
- Includes health check endpoint for container orchestration

## API Usage

The Counter API provides the following endpoints:

### Public Counter Endpoints

#### Get Total Count
- **Endpoint**: `GET /get-total/{id}`
- **Description**: Retrieves the current total count for the specified ID. If the ID does not exist, it initializes it with a total of 0.
- **Example**: `GET /get-total/testID`

#### Increment Count by One
- **Endpoint**: `POST /increment/{id}`
- **Description**: Increments the total count associated with the specified ID by 1. If the ID does not exist, it initializes it with a total of 1.
- **Example**: `POST /increment/testID`

#### Increase Count by a Specified Value
- **Endpoint**: `POST /increase/{id}`
- **Description**: Increases the total count associated with the specified ID by the value provided in the JSON payload. If the ID does not exist, it initializes it with the specified total.
- **Payload**:
  ```json
  {
    "value": 50
  }
  ```
- **Example**: `POST /increase/testID`

### User-Specific Counter Endpoints

#### Get User Counter
- **Endpoint**: `GET /get-total/{user_hash}/{id}`
- **Description**: Retrieves the count for a counter owned by a specific user.

#### Increment User Counter
- **Endpoint**: `POST /increment/{user_hash}/{id}`
- **Description**: Increments a counter owned by a specific user.

#### Increase User Counter
- **Endpoint**: `POST /increase/{user_hash}/{id}`
- **Description**: Increases a counter owned by a specific user by the specified value.

### Statistics

#### Global Stats
- **Endpoint**: `GET /stats`
- **Description**: Returns statistics including `total_counters`, `total_count`, `last_activity`, `top_counters` and `recently_updated`. Shows user-specific stats when authenticated.

## Web Interface

The application includes a web interface for user management and counter administration:

- **Landing page**: `/` - Public homepage
- **Login**: `/login` - User authentication
- **Sign up**: `/signup` - User registration  
- **Dashboard**: `/dashboard` - User dashboard with counter management (requires login)

### Authentication

- Default admin user is created on first run
- If `ADMIN_PASSWORD` environment variable is not set, a temporary password is generated and printed to console
- Users can sign up for accounts to manage their own counters
- Sessions last 2 days with rolling expiration

## Local Development

### Development Server

Run locally without Docker using Flask's development server:

**Windows (PowerShell)**
```powershell
$env:DATABASE = ".\counters.db"
$env:SECRET_KEY = "change-me"
$env:ADMIN_PASSWORD = "set-admin-pass"
$env:SESSION_COOKIE_SECURE = "0"
$env:SESSION_COOKIE_SAMESITE = "Strict"
$env:TRUSTED_PROXY_DEPTH = "0"
python run.py
```

**macOS/Linux**
```bash
export DATABASE=./counters.db
export SECRET_KEY=change-me
export ADMIN_PASSWORD=set-admin-pass
export SESSION_COOKIE_SECURE=0
export SESSION_COOKIE_SAMESITE=Strict
export TRUSTED_PROXY_DEPTH=0
python run.py
```

### Production Server (Local)

For production-like testing with Gunicorn:

```bash
# Install gunicorn
pip install gunicorn

# Run with Gunicorn
gunicorn -w 4 -k gthread --threads 4 -b 0.0.0.0:5000 wsgi:app
```

## Testing

Run the automated smoke tests to validate the API endpoints. The tests use Flask's test client and a temporary SQLite database, so you don't need to start the server.

### Windows (PowerShell)

```powershell
cd C:\Users\admin\Documents\GitHub\api.countify.xyz
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -U pip flask argon2-cffi
python tests/smoke_cli.py
```

### macOS/Linux

```bash
cd /path/to/api.countify.xyz
python -m venv .venv
source .venv/bin/activate
pip install -U pip flask argon2-cffi
python tests/smoke_cli.py
```

The smoke test validates:
- Landing page loads
- Counter initialization to 0
- Counter increment functionality  
- Counter increase by value functionality
- Stats endpoint structure

## Project Structure

```
├── app/                    # Main application package
│   ├── __init__.py        # Application factory
│   ├── models.py          # Database models and operations
│   ├── auth.py            # Authentication routes and helpers
│   ├── api.py             # API routes
│   ├── main.py            # Main web interface routes
│   └── utils.py           # Utility functions
├── templates/             # Jinja2 templates
├── static/               # Static files (CSS, JS, images)
├── tests/                # Test suite
├── debug/                # Admin tools
├── run.py                # Application entry point
└── readme.md             # This file
```

## Security Features

- **CSRF Protection**: All forms include CSRF tokens to prevent cross-site request forgery
- **Rate Limiting**: Login attempts are rate-limited with automatic lockouts
- **Secure Sessions**: Sessions use secure cookies with proper SameSite policies
- **Password Hashing**: Uses Argon2id for password hashing (falls back to PBKDF2)
- **Content Security Policy**: Strict CSP headers to prevent XSS attacks
- **Security Headers**: Comprehensive security headers including X-Frame-Options, X-Content-Type-Options

## Admin Tools

The `debug/` folder contains administrative tools:

- **Reset admin password**: `python debug/reset_admin_password.py`

See `debug/README.md` for detailed documentation.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE` | `counters.db` | Path to SQLite database file |
| `SECRET_KEY` | *generated* | Flask secret key for sessions |
| `ADMIN_PASSWORD` | *generated* | Admin user password (printed to console if not set) |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` for HTTPS environments |
| `SESSION_COOKIE_SAMESITE` | `Strict` | Cookie SameSite policy |
| `TRUSTED_PROXY_DEPTH` | `1` | Number of trusted proxy hops for client IP detection |
| `ENABLE_HSTS` | `0` | Set to `1` to enable HSTS headers (HTTPS only) |
| `LOGIN_MAX_ATTEMPTS` | `5` | Maximum login attempts before lockout |
| `LOGIN_WINDOW_MINUTES` | `10` | Time window for counting login attempts |
| `LOGIN_LOCK_MINUTES` | `10` | Duration of account lockout |

## Contributing

Please read [CONTRIBUTING.md](#) for details on our code of conduct, and the process for submitting pull requests to us.

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.

---
