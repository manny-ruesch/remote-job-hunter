# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RJH_HOME=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE MANIFEST.in *.py ./
COPY rjh ./rjh
COPY dashboard ./dashboard
RUN pip install .

# Jamais en root ; /data = profil, .env (clés) et base SQLite, à garder sur un disque LOCAL.
RUN useradd --create-home --uid 10001 rjh && mkdir -p /data && chown rjh:rjh /data
USER rjh
VOLUME ["/data"]
EXPOSE 5070

# Simple test de port : le Command Center exige un jeton, une requête HTTP sans jeton serait refusée (donc « malade » à tort).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 5070), 3).close()" || exit 1

# Pas d'ENTRYPOINT : `docker compose run --rm command-center rjh init` remplace simplement la commande.
CMD ["rjh", "serve", "--host", "0.0.0.0", "--port", "5070"]
