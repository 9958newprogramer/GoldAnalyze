"""Container health probe for the Redis-backed Worker."""

from __future__ import annotations

from redis import Redis

from app.config import settings


def main() -> None:
    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        protocol=settings.redis_protocol,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        if client.ping() is not True:
            raise SystemExit("Redis ping failed")
    finally:
        client.close()


if __name__ == "__main__":
    main()
