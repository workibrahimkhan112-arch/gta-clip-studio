from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.core.config import ROOT, STORAGE_DIR, VERSION, init_directories
from backend.database import store
from backend.workers.jobs import runner


def local_host(value: str | None) -> bool:
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_directories()
    handler = RotatingFileHandler(STORAGE_DIR / "studio.log", maxBytes=2 * 1024**2, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("backend")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    store.initialize()
    from backend.core import usage
    usage.initialize()
    runner.start()
    try:
        yield
    finally:
        runner.close()
        from backend.core import chatgpt_auth
        chatgpt_auth.close()
        logger.removeHandler(handler)
        handler.close()


app = FastAPI(title="GTA Clip Studio", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def localhost_only(request: Request, call_next):
    # Loopback binding is necessary but not sufficient: Host checks prevent DNS
    # rebinding, and Origin/Fetch-Site checks reject drive-by localhost requests.
    try:
        parsed_host = urlparse(f"http://{request.headers.get('host', '')}")
        if parsed_host.username or parsed_host.password or parsed_host.path or parsed_host.query or parsed_host.fragment:
            return JSONResponse({"detail": "Invalid local request host"}, status_code=403)
        host = parsed_host.hostname
        origin = request.headers.get("origin")
        parsed_origin = urlparse(origin) if origin else None
        safe_origin = not parsed_origin or (parsed_origin.scheme == "http" and local_host(parsed_origin.hostname)
                    and not parsed_origin.username and not parsed_origin.password and not parsed_origin.path
                    and not parsed_origin.query and not parsed_origin.fragment)
    except ValueError:
        return JSONResponse({"detail": "Invalid local request host"}, status_code=403)
    client = request.client.host if request.client else None
    if not local_host(host) or not local_host(client) or not safe_origin or request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "GTA Clip Studio accepts localhost requests only"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(RuntimeError)
async def runtime_error(request, exc):
    logging.getLogger("backend").exception("Request failed", exc_info=exc)
    return JSONResponse({"detail": str(exc)}, status_code=503)


from backend.api.routes import router
app.include_router(router)

# A Vite build is optional in development and served from the same loopback
# origin in production. Unknown /api URLs must never become the SPA index.
dist = ROOT / "frontend" / "dist"
if (dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")


@app.get("/{requested_path:path}", include_in_schema=False)
def frontend(requested_path: str):
    from fastapi import HTTPException
    if requested_path == "api" or requested_path.startswith("api/"):
        raise HTTPException(404, "API endpoint not found")
    candidate = (dist / requested_path).resolve()
    if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
        return FileResponse(candidate)
    if (dist / "index.html").is_file():
        return FileResponse(dist / "index.html")
    return JSONResponse({"message": "Backend running. Start the interface with npm run dev, or npm run build for a production bundle.",
                         "api": "/docs"})
