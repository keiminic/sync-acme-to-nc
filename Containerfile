FROM mcr.microsoft.com/playwright/python:v1.57.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY main.py /app/main.py

RUN pip install --no-cache-dir -r requirements.txt && mkdir /data

CMD ["python", "main.py"]