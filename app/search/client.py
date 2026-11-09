"""The app's one Elasticsearch client.

Nothing here raises when Elasticsearch is missing or down: callers check for
None or es_available() and fall back.
"""
import logging
import os
import threading

from elasticsearch import ApiError, Elasticsearch, TransportError

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def get_client():
    """Return the shared client, or None if it isn't configured."""
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("ES_URL")
    api_key = os.environ.get("ES_API_KEY")
    if not url or not api_key:
        return None

    with _lock:
        if _client is None:
            try:
                _client = Elasticsearch(
                    hosts=url,
                    api_key=api_key,
                    request_timeout=5,
                    max_retries=1,
                )
            except ValueError:
                # Don't log the exception: it may quote the credentials.
                logger.error("ES_URL or ES_API_KEY is malformed")
                return None
    return _client


def es_available():
    """Return True if Elasticsearch answers a request this API key can make.

    Uses a count rather than ping(): ping() needs a cluster privilege that a
    key scoped to the talks* and occupancy* indices doesn't have, so it would
    report Elasticsearch as down when it's fine.
    """
    client = get_client()
    if client is None:
        return False
    try:
        client.options(request_timeout=2, max_retries=0).count(index="talks*")
    except (ApiError, TransportError):
        return False
    return True
