FROM python:3.12-slim
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
ARG APP_VERSION=dev
# UID 1000 has no home directory, and gunicorn writes its control socket there.
ENV PORT=8080 APP_VERSION=$APP_VERSION PYTHONUNBUFFERED=1 HOME=/tmp
# By number so runAsNonRoot can check it.
USER 1000
EXPOSE 8080
# sh expands $PORT; exec lets gunicorn receive SIGTERM so rollouts don't drop requests.
CMD ["sh", "-c", "exec gunicorn -b 0.0.0.0:${PORT} app:app"]
