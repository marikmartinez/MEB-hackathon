FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
ARG APP_VERSION=dev
ENV PORT=8080 APP_VERSION=$APP_VERSION
# A numeric non-root user, so runAsNonRoot can check it.
USER 1000
EXPOSE 8080
# Shell form so $PORT expands; exec so gunicorn gets SIGTERM and drains cleanly.
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --graceful-timeout 20 --access-logfile - app:app
