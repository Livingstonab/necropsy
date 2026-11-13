# NECROPSY — one container: Flask + SQLite, served by Waitress.
#
# The image bakes in the seeded graveyard so the wall renders on first boot with
# no network and no keys (P4). A GITHUB_TOKEN / LLM_API_KEY in the environment
# enables faster live analysis and model-written narratives; neither is required.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/data/necropsy.db \
    PORT=8000

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The application and its committed fixtures.
COPY necropsy/ necropsy/
COPY pyproject.toml README.md LICENSE ./

# Build the graveyard into a seed database inside the image, then copy it to the
# runtime data dir at start (a mounted volume would otherwise shadow it empty).
RUN python -m necropsy.cli --database /app/necropsy.seed.db init-db \
 && python -m necropsy.cli --database /app/necropsy.seed.db seed-graveyard

COPY docker-entrypoint.sh /usr/local/bin/necropsy-entrypoint
RUN chmod +x /usr/local/bin/necropsy-entrypoint

EXPOSE 8000
ENTRYPOINT ["necropsy-entrypoint"]
