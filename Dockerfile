# Sentinelle — backend Django pour un VPS (WebSockets avec Django Channels).
#   docker build -t sentinelle .
#   docker run -d -p 8000:8000 -v sentinelle-donnees:/donnees --env-file .env sentinelle
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SENTINELLE_BD=/donnees/sentinelle.db \
    SENTINELLE_WEBSOCKET=1
COPY requirements.txt requirements-vps.txt ./
RUN pip install --no-cache-dir -r requirements-vps.txt
COPY . .
RUN useradd --system --uid 1000 sentinelle && mkdir -p /donnees && chown -R sentinelle /donnees /app \
    && python manage.py collectstatic --noinput
USER sentinelle
EXPOSE 8000
# Migrations au démarrage, puis uvicorn (--proxy-headers : vraie adresse IP transmise par Caddy).
CMD ["sh", "-c", "python manage.py migrate --noinput && uvicorn sentinelle.asgi:application --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
