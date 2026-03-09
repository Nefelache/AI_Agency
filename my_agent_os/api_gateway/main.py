"""
Neural Gateway — The single entry point for all traffic.

Dual-channel architecture:
  /mobile  -> lightweight webhook for on-the-go approvals (Slack, WeCom)
  /console -> heavy-duty terminal for deep-dive sessions at the desk
  /        -> web terminal UI served as static files
  /memory  -> memory management API
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from my_agent_os.api_gateway.routes import mobile_webhook, web_terminal, memory_api
from my_agent_os.config.settings import settings

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize memory engine. Shutdown: close it."""
    from my_agent_os.agent_core.llm_client import call_llm
    from my_agent_os.agent_core.router_engine import set_memory_engine, set_crew_orchestrator
    from my_agent_os.memory_layer.engine import MemoryEngine
    from my_agent_os.agent_core.crew.orchestrator import CrewOrchestrator

    engine = MemoryEngine(
        db_path=settings.MEMORY_DB_PATH,
        llm=call_llm,
        top_k=settings.MEMORY_RETRIEVAL_TOP_K,
        decay_days=settings.MEMORY_PRIORITY_DECAY_DAYS,
        max_injection_chars=settings.MEMORY_MAX_INJECTION_CHARS,
    )
    await engine.initialize()

    crew = CrewOrchestrator(llm=call_llm)

    set_memory_engine(engine)
    set_crew_orchestrator(crew)
    memory_api.set_engine(engine)

    yield

    await engine.close()


app = FastAPI(
    title="Agent OS — Neural Gateway",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mobile_webhook.router, prefix="/mobile", tags=["Mobile"])
app.include_router(web_terminal.router, prefix="/console", tags=["Console"])
app.include_router(memory_api.router, prefix="/memory", tags=["Memory"])


@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "alive", "vibe": "clean", "memory": "active"}
