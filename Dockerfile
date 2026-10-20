FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8000 \
    NOTEBOOKLM_DISABLE_HEADLESS_REFRESH=1 \
    PIP_NO_CACHE_DIR=1

RUN mkdir -p /data

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY VERSION .

EXPOSE 8000
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4)"

# --proxy-headers: honor X-Forwarded-Proto/Host from NPM so request-derived
# base URLs come out https://domain instead of http://container.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
