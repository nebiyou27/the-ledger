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
    && pip install \
        asyncpg==0.29.0 \
        anthropic==0.39.0 \
        pydantic==2.12.5 \
        langgraph==0.2.76 \
        faker==25.9.2 \
        reportlab==4.4.10 \
        openpyxl==3.1.5 \
        fastmcp==3.1.1 \
        fastapi==0.135.1 \
        uvicorn==0.42.0 \
        python-multipart==0.0.20 \
        python-dotenv==1.2.2 \
        httpx==0.28.1 \
        requests==2.32.5

COPY ledger ./ledger
COPY src ./src
COPY scripts ./scripts
COPY datagen ./datagen
COPY sql ./sql

CMD ["python", "-m", "uvicorn", "ledger.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
