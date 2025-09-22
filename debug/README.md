# Debug and Admin Tools

This folder contains administrative tools for the Counter API application.

## Admin Management Scripts

### `reset_admin_password.py`
Resets the admin user password to a new value you specify.

**Usage:**
```bash
python debug/reset_admin_password.py
```
- Prompts for new password (hidden input)
- Requires password confirmation
- Updates the admin user in the database
- Uses Argon2 hashing if available, falls back to PBKDF2

## Common Admin Tasks

1. **Reset forgotten password**: Use `reset_admin_password.py`

## Environment Variables

These scripts respect the following environment variables:
- `DATABASE`: Path to SQLite database (default: `counters.db`)

## Dependencies

All scripts require:
- `sqlite3` (built-in)
- `werkzeug` (for password hashing)
- `argon2-cffi` (optional, for Argon2 hashing)