# syntax=docker/dockerfile:1

# ---- build stage: compile wheels, then throw the toolchain away -------------
FROM python:3.12-alpine AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build-only deps; not present in the final image.
RUN apk add --no-cache build-base libffi-dev

COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt


# ---- runtime stage: no compiler, no pip cache, no tests ---------------------
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Unprivileged user; the app never writes to disk.
RUN adduser -D -H -u 10001 app

WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY app.py ./
COPY templates/ ./templates/
COPY static/ ./static/

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status==200 else 1)"

# Threads (not extra workers) keep memory small while still serving concurrent reads.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "90", \
     "--graceful-timeout", "20", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
