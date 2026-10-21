FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends cdparanoia ffmpeg eject \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ripper /app/ripper
COPY entrypoint.sh /entrypoint.sh
# Files can arrive owner-only depending on how they were unzipped; the app runs as a non-root user.
RUN chmod -R a+rX /app && chmod 755 /entrypoint.sh

WORKDIR /app
ENV PYTHONUNBUFFERED=1
EXPOSE 8686
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8686/api/state',timeout=4)" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
