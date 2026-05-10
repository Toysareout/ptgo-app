FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY skycoach/ ./skycoach/

EXPOSE 8001

CMD ["uvicorn", "skycoach.main:app", "--host", "0.0.0.0", "--port", "8001"]
