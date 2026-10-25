FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
COPY services/api/requirements.txt /app/api-requirements.txt
COPY services/ocr/requirements.txt /app/ocr-requirements.txt
RUN pip install --no-cache-dir -r /app/api-requirements.txt \
    && pip install --no-cache-dir -r /app/ocr-requirements.txt
COPY scanner-engine /app/scanner-engine
COPY services/ocr /app/services/ocr
ENV PYTHONPATH=/app/services/ocr:/app/scanner-engine
EXPOSE 8001
CMD ["uvicorn", "scip_ocr.main:app", "--host", "0.0.0.0", "--port", "8001"]
