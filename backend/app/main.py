from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import bilibili
from app.core.config import get_settings
from app.db.session import engine
from app.models import Base  # ensures all models are imported

settings = get_settings()
Base.metadata.create_all(bind=engine)

app = FastAPI(title="ADHD Personal Dashboard", openapi_url=f"{settings.api_prefix}/openapi.json")
app.include_router(bilibili.router, prefix=settings.api_prefix)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def healthcheck() -> dict:
    return {"status": "ok"}
