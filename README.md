# Expense Tracker

A polished Flask + MongoDB expense tracker with authentication, insights, charts, exports, and a responsive dashboard.

## Features
- User registration and login
- Expense and income tracking
- Budget insights and balance summaries
- Charts for category and monthly trends
- Filtered, chronological CSV export
- Search, date/category/amount filters, and pagination
- Persistent dark/light theme
- CSRF-protected forms and password reset
- Per-currency totals (unlike currencies are never added together)

## Setup
1. Create and activate a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Configure environment variables:
   - `SECRET_KEY` (use a long, unique random value; see `.env.example`)
   - `MONGO_URI`
   - `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, and `MAIL_PASSWORD` for password-reset email
4. Run: `python app.py`

The dashboard uses the configured income currency for budget, category, and monthly
metrics. Expenses entered in other currencies remain visible in transactions and
in a separate currency breakdown; the app does not guess exchange rates.

For Docker Compose, copy `.env.example` to `.env`, replace `SECRET_KEY` with a
secure random value, then run `docker compose up --build`.

## Testing
Run:

```bash
python -m pytest -q
```
