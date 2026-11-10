"""FastAPI app for the NonStop demo API. The contract is openapi.yaml at the repository root.

Run locally: uv run uvicorn app.main:app --reload --no-access-log
"""

import logging
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException

from app.api import feed, home, onboarding, papers
from app.api.errors import ApiError, problem
from app.catalog import Catalog, CatalogUnavailable

access_logger = logging.getLogger("nonstop.access")
error_logger = logging.getLogger("nonstop.error")


def parse_origins(raw: str | None) -> list[str]:
    return [origin.strip() for origin in (raw or "").split(",") if origin.strip()]


def create_app(
    catalog: Catalog | None = None, cors_origins: list[str] | None = None
) -> FastAPI:
    load_dotenv()
    # The generated schema would drift from openapi.yaml, so it is not served.
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.catalog = catalog or Catalog()
    for router in (onboarding.router, home.router, feed.router, papers.router):
        app.include_router(router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            cors_origins
            if cors_origins is not None
            else parse_origins(os.getenv("CORS_ALLOWED_ORIGINS"))
        ),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        # Only the path is logged: query strings carry interests and exclude.
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            access_logger.info(
                "%s %s %d %.1fms", request.method, request.url.path, status, elapsed
            )

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return problem(exc.status, exc.code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return problem(400, "INVALID_PARAMETER")

    @app.exception_handler(CatalogUnavailable)
    async def catalog_unavailable(request: Request, exc: CatalogUnavailable):
        error_logger.warning("Firestore unavailable: %s", exc)
        return problem(503, "SERVICE_UNAVAILABLE")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # Unknown routes and methods. The contract has no code for them, so the closest
        # one is used with the real status.
        return problem(exc.status_code, "INVALID_PARAMETER")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        error_logger.error("Unhandled %s on %s", type(exc).__name__, request.url.path)
        return problem(500, "INTERNAL_ERROR")

    return app


def configure_logging() -> None:
    # uvicorn's own access log prints query strings; it is disabled even without the flag.
    logging.getLogger("uvicorn.access").disabled = True
    # Only this app's loggers go to INFO; libraries such as httpx stay quiet.
    logger = logging.getLogger("nonstop")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


configure_logging()
app = create_app()
