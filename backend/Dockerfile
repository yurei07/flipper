FROM python:3.12-slim

WORKDIR /app

# Доверяем базовым образам, pip офлайн-кэш не требуется
COPY pyproject.toml .
COPY flipper ./flipper
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "flipper.app.main:app", "--host", "0.0.0.0", "--port", "8000"]