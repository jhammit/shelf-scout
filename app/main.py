"""HTTP API + static web app.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
Then open http://<this-computer's-IP>:8000 on your phone.
"""
from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .cache import Cache
from .config import ROOT, settings
from .models import BookResult, BuyerProfile, ScanResult
from .pipeline import Scanner
from .vision import VisionError

try:  # iPhone photos can arrive as HEIC
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
MAX_UPLOAD = 25 * 1024 * 1024
STATIC = ROOT / "static"

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(timeout=20, headers={"User-Agent": settings.user_agent})
    cache = Cache(settings.cache_path)
    cache.purge()
    state["http"], state["scanner"] = http, Scanner(http, cache)
    yield
    await http.aclose()


app = FastAPI(title="Shelf Scout", version="1.0", lifespan=lifespan)


def require_password(x_app_password: str = Header(default="")):
    if settings.app_password and not hmac.compare_digest(x_app_password, settings.app_password):
        raise HTTPException(status_code=401, detail="Wrong or missing app password.")


def _profile(raw: str) -> BuyerProfile:
    try:
        return BuyerProfile(**(json.loads(raw) if raw else {}))
    except (ValueError, ValidationError) as e:
        raise HTTPException(status_code=422, detail=f"Bad settings: {e}")


@app.get("/api/status")
async def status():
    return state["scanner"].sources()


@app.post("/api/scan", response_model=ScanResult, dependencies=[Depends(require_password)])
async def scan(image: UploadFile = File(...), profile: str = Form(default="")):
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload.")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Photo is larger than 25 MB.")
    try:
        return await state["scanner"].scan(data, _profile(profile))
    except VisionError as e:
        raise HTTPException(status_code=502, detail=f"Couldn't read the photo: {e}")
    except OSError as e:  # PIL couldn't decode it
        raise HTTPException(status_code=400, detail=f"Unreadable image file: {e}")


class IsbnRequest(BaseModel):
    isbn: str
    profile: BuyerProfile = BuyerProfile()


class IsbnResponse(BaseModel):
    book: BookResult
    warnings: list[str]


@app.post("/api/isbn", response_model=IsbnResponse, dependencies=[Depends(require_password)])
async def isbn(req: IsbnRequest):
    try:
        book, warnings = await state["scanner"].check_isbn(req.isbn, req.profile)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return IsbnResponse(book=book, warnings=warnings)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
