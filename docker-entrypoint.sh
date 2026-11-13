#!/usr/bin/env sh
# Start script for the container (R-5.4, E13).
#
# If the data directory has no database yet (first boot, or a fresh volume),
# copy the image-baked seed in so the graveyard renders immediately. An existing
# database is left alone — re-seeding is idempotent and never touches a visitor's
# own analyses (R-5.5). Then apply migrations and serve.

set -eu

: "${DATABASE_PATH:=/data/necropsy.db}"
: "${PORT:=8000}"

DATA_DIR=$(dirname "$DATABASE_PATH")
mkdir -p "$DATA_DIR"

if [ ! -f "$DATABASE_PATH" ]; then
  echo "necropsy: no database at $DATABASE_PATH; installing the seeded graveyard"
  cp /app/necropsy.seed.db "$DATABASE_PATH"
fi

# Idempotent: applies any pending migrations, re-seeds missing demo rows, leaves
# user analyses and existing demo rows untouched.
python -m necropsy.cli init-db
python -m necropsy.cli seed-graveyard || echo "necropsy: seed step reported a non-zero exit; continuing with whatever is present"

echo "necropsy: serving on 0.0.0.0:$PORT"
exec python -m necropsy.cli serve --host 0.0.0.0 --port "$PORT"
