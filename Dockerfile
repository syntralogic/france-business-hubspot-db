FROM python:3.12-slim
# postgresql-client: scripts/init_db.sh and backup use psql/pg_dump; curl + unzip: scripts/initial_load.sh downloads the SIRENE files
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY pyproject.toml ./
COPY src ./src
COPY sql ./sql
COPY scripts ./scripts
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
RUN useradd --uid 1000 --create-home app && mkdir -p reports data/raw data/staging data/exports backups && chown -R app /app
USER app
CMD ["bash", "scripts/run_weekly.sh"]
