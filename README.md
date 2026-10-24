# PayVanta AI – Local Setup

No Firebase. No cloud keys. Data stored in `data/*.json` files.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy env file
cp .env.example .env
# Edit SECRET_KEY in .env

# 3. Run
python app.py
# OR
python main.py
```

App runs at http://127.0.0.1:5000

## Default Accounts

| Role         | Phone          | Password    |
|--------------|----------------|-------------|
| Admin        | +919999999999  | Admin@123   |
| Bank Officer | +919888888888  | Officer@123 |
| Demo User    | +919876543210  | demo1234    |

## OTP in Dev Mode

With `DEV_MODE=true` (default), OTP is printed in the terminal — no SMS needed.

## Data Storage

All data lives in `data/` folder as JSON files:
- `data/users.json`
- `data/accounts.json`
- `data/transactions.json`
- etc.

Delete any file to reset that collection. Delete the whole `data/` folder to start fresh.

## Production

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Set `FLASK_DEBUG=0` and `SESSION_COOKIE_SECURE=true` in production.
