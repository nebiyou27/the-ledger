FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY ledger ./ledger
COPY src ./src
COPY scripts ./scripts
COPY datagen ./datagen
COPY sql ./sql

CMD ["python", "-m", "uvicorn", "ledger.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
