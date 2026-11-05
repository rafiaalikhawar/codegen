"""Application factory: ``uvicorn app.main:app``."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api, web
from .database import Database
from .engine import AppError
from .meta import META

BASE_DIR = Path(__file__).resolve().parent


def _wants_json(request: Request):
    return request.url.path.startswith("/api") or request.url.path == "/health"


def create_app(database_url=None):
    app = FastAPI(title=f"{META['domain']} API", docs_url="/docs")
    app.state.db = Database(database_url)

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        if _wants_json(request):
            return JSONResponse({"detail": exc.message, "errors": exc.errors}, status_code=exc.status)
        return web.render_error(request, exc.status, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = {}
        for err in exc.errors():
            loc = [str(p) for p in err["loc"] if p not in ("body", "query", "path")]
            errors[".".join(loc) or "_"] = err["msg"]
        return JSONResponse({"detail": "Validation failed", "errors": errors}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if _wants_json(request):
            return JSONResponse({"detail": exc.detail, "errors": {}}, status_code=exc.status_code)
        return web.render_error(request, exc.status_code, str(exc.detail))

    @app.get("/health")
    def health():
        return {"status": "ok", "domain": META["domain"]}

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.include_router(api.router)
    app.include_router(web.router)
    return app


app = create_app()
