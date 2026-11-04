#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
# Railway assigns PORT. Bind all interfaces for Railway's public networking.
exec waitress-serve --host=0.0.0.0 --port="${PORT:-5000}" app:app
