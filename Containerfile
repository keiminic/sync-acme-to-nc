FROM mcr.microsoft.com/playwright/python:v1.57.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir playwright==1.57.0 python-dotenv==1.2.1 pyotp==2.9.0 && mkdir /data

COPY main.py /app/main.py

CMD ["python", "main.py"]