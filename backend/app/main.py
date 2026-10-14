import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = Store(settings)
    try:
        app.state.store.sync()
    except Exception:
        # Keep serving: /api/health reports the error and POST /api/sync can retry.
        pass
    yield


app = FastAPI(
    title="Event Sync Service",
    description="Reconciles CRM and Calendar meetings into a unified view with field-level provenance.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
