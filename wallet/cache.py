"""Optional Redis cache for read-only summaries. Decisions always read SQLite."""

import json
import hashlib
from . import config


def cached_summary(factory):
    if not config.REDIS_URL:
        return {**factory(), "cache": "disabled"}
    try:
        import redis

        client = redis.Redis.from_url(
            config.REDIS_URL, socket_connect_timeout=0.3, socket_timeout=0.3
        )
        key = (
            "careflow:wallet:summary:v2:"
            + hashlib.sha256(str(config.DB_PATH.resolve()).encode()).hexdigest()[:16]
        )
        existing = client.get(key)
        if existing:
            return {**json.loads(existing), "cache": "redis; up to 30 seconds old"}
        value = factory()
        client.setex(key, 30, json.dumps(value))
        return {**value, "cache": "redis"}
    except Exception:
        return {**factory(), "cache": "unavailable; SQLite fallback"}
