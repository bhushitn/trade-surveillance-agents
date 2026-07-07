FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY frontend ./frontend
COPY eval ./eval

RUN pip install --no-cache-dir ".[serve]"

ENV LOCAL_STATE_DIR=/app/.local_state
EXPOSE 8000

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
