import os
from collections.abc import Callable

import psycopg
import redis
from fastapi import FastAPI, Response, status
from neo4j import GraphDatabase

CONNECT_TIMEOUT_SECONDS = 3

app = FastAPI(title="CodeReview Agent")


def _check_postgres() -> None:
    with psycopg.connect(
        os.environ["DATABASE_URL"], connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        conn.execute("SELECT 1")


def _check_redis() -> None:
    client = redis.Redis.from_url(
        os.environ["REDIS_URL"], socket_connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    try:
        client.ping()
    finally:
        client.close()


def _check_neo4j() -> None:
    auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    with GraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=auth, connection_timeout=CONNECT_TIMEOUT_SECONDS
    ) as driver:
        driver.verify_connectivity()


STORE_CHECKS: dict[str, Callable[[], None]] = {
    "postgres": _check_postgres,
    "redis": _check_redis,
    "neo4j": _check_neo4j,
}


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    """Report whether the app can reach each store; 503 if any is unreachable."""
    stores: dict[str, str] = {}
    for name, check in STORE_CHECKS.items():
        try:
            check()
        except Exception as exc:  # any failure means the store is unreachable
            stores[name] = f"error: {type(exc).__name__}"
        else:
            stores[name] = "ok"
    if any(result != "ok" for result in stores.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return stores
