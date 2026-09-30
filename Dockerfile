FROM python:3.12-slim AS builder

WORKDIR /app

# Install system build dependencies in case a package has to compile C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY ./src ./src

# Install wheel & dependencies into a virtual environment or standard site-packages
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

FROM python:3.12-slim AS runtime

WORKDIR /app

# Standard Python environment settings
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:$PATH"

# Runtime-only system libraries (e.g. libpq for Postgres drivers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages and executables
COPY --from=builder /usr/local /usr/local
COPY --from=builder /app/src ./src

# Create a non-root user and assign permissions
RUN useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "reviewer.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
