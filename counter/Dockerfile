# Build from the repo root: docker build -f counter/Dockerfile .
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY counter/requirements.txt counter/requirements.txt
RUN pip install --no-cache-dir -r counter/requirements.txt
# Model, frames and code. Tests, samples and dev tools stay out of the image.
COPY counter/__init__.py counter/model.py counter/metrics.py counter/replay.py counter/server.py counter/
COPY counter/models/ counter/models/
COPY counter/frames/ counter/frames/
ARG APP_VERSION=dev
ENV PORT=8080 APP_VERSION=$APP_VERSION
USER 1000
EXPOSE 8080
# One worker: the replay loop runs once per process. Threads serve /count etc.
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --graceful-timeout 20 --access-logfile - "counter.server:create_app()"
