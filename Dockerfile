FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 connector
COPY bank_connector ./bank_connector
COPY connector.py .
USER connector
EXPOSE 3000
CMD ["python", "connector.py"]
