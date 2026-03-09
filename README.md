# Agent OS — Local AI Agent Hub

A fully local, privacy-first AI command center for high-performance individuals.
Designed to eliminate **Task Paralysis** through calm, intelligent automation.

## Architecture

```
my_agent_os/
├── api_gateway/          Neural Gateway (FastAPI, dual-channel)
├── agent_core/           Brain (Intent Router + YAML Prompt Engine)
├── memory_layer/         Memory (Document Parser + Local Vector DB)
├── skills_layer/         Limbs (Stateless, hot-pluggable tool plugins)
├── config/               Settings + Environment
└── tests/                Unit tests
```

## Design Philosophy

| Principle | Implementation |
|-----------|---------------|
| **Clean Fit Minimalism** | Zero visual noise; prompts in YAML, not code |
| **Control Aesthetic** | No anxiety signals; silent recalculation on failure |
| **Three-Dimensional Decisions** | Global Data + Personal Preferences + Current State |
| **Extreme Decoupling** | Brain / Limbs / Memory are fully independent layers |

## Quick Start

```bash
# 1. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the gateway
uvicorn my_agent_os.api_gateway.main:app --reload

# 4. Run tests
pytest my_agent_os/tests/ -v
```

## Adding a New Skill

1. Create a file in `my_agent_os/skills_layer/tools/` (e.g. `weather.py`).
2. Subclass `Skill`, apply the `@register` decorator.
3. Done. The router can now dispatch to it. No trunk code changes needed.

## Customizing for a New Client

1. Duplicate `agent_core/prompts/system_prompts.yaml`.
2. Override the `preferences:` block with client-specific constraints.
3. Point `settings.py` to the new file. Ship it.
