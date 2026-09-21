# syntax=docker/dockerfile:1
#
# Self-distilling payment recovery agent.
#
#   docker build -t payment-recovery .                 # runtime image
#   docker build --target test -t payment-recovery:test .   # runs the full suite on 3.11
#   docker run --rm -p 8000:8000 payment-recovery      # dashboard on http://localhost:8000
#   docker run --rm payment-recovery python -m evals.run --no-open   # offline eval
#
# No secrets are baked in. Provider keys are optional and, if supplied at run
# time (e.g. --env-file .env), must be sandbox keys: the app refuses to boot
# with rzp_live_/sk_live_ credentials.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/app \
    DATABASE_URL=sqlite:////app/data/payment_recovery.db

WORKDIR /app

# Exact pins resolved for CPython 3.11 / linux x86_64 (see requirements.lock).
COPY requirements.lock ./
RUN pip install -r requirements.lock

# Source only. The app runs from /app: prompts/ and evals/ artefacts are read
# relative to the source tree.
COPY pyproject.toml config.py ./
COPY agentcore ./agentcore
COPY providers ./providers
COPY recovery ./recovery
COPY llm ./llm
COPY evals ./evals
COPY prompts ./prompts

# --- test stage: the complete suite, inside the 3.11 image -----------------
FROM base AS test
COPY tests ./tests
RUN python -m pytest -q -p no:cacheprovider

# --- runtime stage (default target) -----------------------------------------
FROM base AS runtime
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "recovery.app:app", "--host", "0.0.0.0", "--port", "8000"]
