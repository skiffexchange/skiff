#!/bin/sh
# LOCAL run only: ./run.sh -> http://127.0.0.1:8850
# Loads ./.env (copy .env.example to .env and fill it; never committed) into the environment.
# Needs a Python 3.12 venv in ./.venv:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# DEV=1 (default here) lets requests from 127.0.0.1 sign in with an X-Dev-Wallet header. Never on a server: guard.py refuses to start a
# production process with DEV set, and a production start command never goes through this script.
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
mkdir -p data
cd engine && DEV="${DEV:-1}" exec ../.venv/bin/uvicorn server:app --host 127.0.0.1 --port "${PORT:-8850}" --log-level warning --no-proxy-headers
