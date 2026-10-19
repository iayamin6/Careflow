"""Opaque operator credentials, role policy, audit identity and request throttling."""

import argparse
import hashlib
import secrets
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.responses import JSONResponse
from .store import connect, utc

actor = ContextVar("careflow_actor", default="local-command")
ROLES = {"admin", "analyst", "support", "operations", "automation"}


def issue(name, role, days=30):
    if role not in ROLES or not name.strip() or not 1 <= days <= 365:
        raise ValueError("Valid operator name, role and 1–365 day expiry required")
    token = secrets.token_urlsafe(36)
    expires = utc((datetime.now(timezone.utc) + timedelta(days=days)).isoformat())
    with connect() as c:
        c.execute(
            "INSERT INTO operators(name,role,token_hash,expires_at,revoked) VALUES(?,?,?,?,0)",
            (name, role, hashlib.sha256(token.encode()).hexdigest(), expires),
        )
    return token


def identity(token):
    if not token or len(token) > 256:
        return None
    with connect() as c:
        row = c.execute(
            "SELECT id,name,role FROM operators WHERE token_hash=? AND revoked=0 AND expires_at>?",
            (hashlib.sha256(token.encode()).hexdigest(), utc()),
        ).fetchone()
    return dict(row) if row else None


def allowed(role, method, path):
    if role == "admin":
        return True
    if path.startswith("/wallet/operators"):
        return False
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    if path == "/wallet/payment/predict":
        return True
    if path.startswith("/wallet/operators"):
        return False
    if (
        path.endswith("/approve")
        or path.endswith("/override")
        or path.endswith("/preferences")
    ):
        return role in ("support", "operations")
    if path.startswith("/wallet/deliveries"):
        return role in ("operations", "automation")
    if path.endswith("/execute") or path == "/wallet/decisions/refresh":
        return role in ("support", "operations", "automation")
    if path.startswith(("/wallet/incidents", "/wallet/complaints")):
        return role in ("support", "operations")
    if path.startswith(
        (
            "/wallet/events",
            "/wallet/customers",
            "/wallet/merchants",
            "/wallet/settlements",
            "/wallet/refunds",
            "/wallet/batch",
            "/wallet/jobs",
            "/wallet/models",
            "/wallet/experiments",
        )
    ):
        return role == "operations"
    return False  # Legacy mutations require admin; no bypass through old endpoints.


def install(app):
    # Document the middleware's Bearer contract in Swagger's Authorize control.
    from fastapi.openapi.utils import get_openapi

    def secured_openapi():
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title, version=app.version, routes=app.routes
            )
            schema.setdefault("components", {}).setdefault("securitySchemes", {})[
                "OperatorToken"
            ] = {"type": "http", "scheme": "bearer"}
            for path, methods in schema["paths"].items():
                if path not in ("/", "/wallet", "/health"):
                    for operation in methods.values():
                        if isinstance(operation, dict):
                            operation["security"] = [{"OperatorToken": []}]
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = secured_openapi
    buckets = defaultdict(deque)

    @app.middleware("http")
    async def protect(request, call_next):
        path = request.url.path
        if path in (
            "/",
            "/wallet",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        ) or path.startswith("/static/"):
            return await call_next(request)
        now = time.monotonic()
        ip = request.client.host if request.client else "unknown"
        bucket = buckets[ip]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 180:
            return JSONResponse(
                {"detail": "Request limit reached; retry in one minute"},
                429,
                headers={"Retry-After": "60"},
            )
        bucket.append(now)
        if len(buckets) > 10000:
            for key in list(buckets):
                if not buckets[key] or buckets[key][-1] < now - 60:
                    buckets.pop(key, None)
        credential = request.headers.get("authorization", "")
        user = identity(credential[7:]) if credential.startswith("Bearer ") else None
        if not user:
            return JSONResponse(
                {"detail": "Sign in with a valid operator token"},
                401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not allowed(user["role"], request.method, path):
            return JSONResponse({"detail": "Your role cannot perform this action"}, 403)
        request.state.operator = user
        context = actor.set(user["name"])
        try:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            actor.reset(context)


if __name__ == "__main__":
    from .store import migrate

    parser = argparse.ArgumentParser(
        description="Create a local operator token, stored only as a hash in SQLite"
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument(
        "--output", required=True, help="New private token file; refuses to overwrite"
    )
    args = parser.parse_args()
    migrate()
    path = Path(args.output)
    with path.open("x") as f:
        path.chmod(0o600)
        f.write(issue(args.name, args.role) + "\n")
    print("Operator credential saved to the specified private file.")
