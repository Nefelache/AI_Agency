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
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from my_agent_os.api_gateway.openclaw_compat import handle_openclaw_gateway_ws
from my_agent_os.api_gateway.routes import mobile_webhook, web_terminal, memory_api, whatsapp, health_ext, audit_api
from my_agent_os.api_gateway.routes import auth_routes, billing, gdpr, voice, slack, admin_routes
from my_agent_os.config.settings import settings
from my_agent_os.version import __version__ as APP_VERSION

_STATIC_DIR = Path(__file__).parent / "static"
_OPENCLAW_STATIC = _STATIC_DIR / "openclaw"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize memory engine. Shutdown: close it."""
    from my_agent_os.agent_core.llm_client import call_llm
    from my_agent_os.agent_core.router_engine import set_memory_engine, set_crew_orchestrator
    from my_agent_os.memory_layer.engine import MemoryEngine
    from my_agent_os.agent_core.crew.orchestrator import CrewOrchestrator
    from my_agent_os.enterprise.audit import prune_retention

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
    gdpr.set_engine(engine)

    from my_agent_os.skills_layer import context as skills_ctx
    skills_ctx.set_memory_engine(engine)

    from my_agent_os.agent_core.router_engine import route as _route_fn
    slack.set_router(_route_fn)

    # Enterprise: prune old audit logs on startup (best-effort)
    try:
        prune_retention()
    except Exception:
        pass

    yield

    await engine.close()


app = FastAPI(
    title="Agent OS — Neural Gateway",
    version=APP_VERSION,
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
app.include_router(whatsapp.router)
app.include_router(health_ext.router, tags=["Health"])
app.include_router(audit_api.router)
app.include_router(auth_routes.router)
app.include_router(billing.router)
app.include_router(gdpr.router)
app.include_router(voice.router)
app.include_router(slack.router)
app.include_router(admin_routes.router)


@app.get("/openclaw/__openclaw/control-ui-config.json")
async def openclaw_control_ui_bootstrap():
    """Bootstrap for official OpenClaw Control UI static app (assistant label + base path)."""
    return {
        "basePath": "/openclaw",
        "assistantName": "Agent OS",
        "assistantAvatar": "",
        "assistantAgentId": "agent-os",
        "serverVersion": APP_VERSION,
    }


@app.get("/openclaw")
async def openclaw_redirect_trailing():
    return RedirectResponse("/openclaw/", status_code=302)


@app.websocket("/openclaw")
async def openclaw_gateway_ws(websocket: WebSocket):
    await handle_openclaw_gateway_ws(websocket)


if _OPENCLAW_STATIC.is_dir():
    app.mount(
        "/openclaw",
        StaticFiles(directory=str(_OPENCLAW_STATIC), html=True),
        name="openclaw_control_ui",
    )


@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/chat")
async def chat_route():
    """Serve the main chat UI for /chat?session=<key> deep-links."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/setup")
async def onboarding():
    return FileResponse(_STATIC_DIR / "onboarding.html")


@app.get("/health")
async def health():
    return {"status": "alive", "vibe": "clean", "memory": "active"}
