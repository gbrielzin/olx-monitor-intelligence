FROM python:3.11-slim

WORKDIR /app

COPY scraper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY scraper .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
