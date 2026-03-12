# Agent OS — Local AI Agent Hub

A fully local, privacy-first AI command center for high-performance individuals.
Designed to eliminate **Task Paralysis** through calm, intelligent automation.

**OpenClaw-inspired**: WhatsApp integration (QR link), enterprise-grade audit, DM/group policies, retry.

## Architecture

```
my_agent_os/
├── api_gateway/          Neural Gateway (FastAPI, dual-channel)
├── agent_core/           Brain (Intent Router + YAML Prompt Engine)
├── memory_layer/         Memory (Document Parser + Local Vector DB)
├── skills_layer/         Limbs (Stateless, hot-pluggable tool plugins)
├── config/               Settings + Environment
├── enterprise/           Audit logging, policies
└── tests/                Unit tests

channels/
└── whatsapp-bridge/      Baileys Node.js bridge (QR code login)
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
# 1. 交互式配置（推荐）
python setup.py

# 2. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the gateway
python run.py

# 5. Run tests
pytest my_agent_os/tests/ -v
```

## Adding a New Skill

1. Create a file in `my_agent_os/skills_layer/tools/` (e.g. `weather.py`).
2. Subclass `Skill`, apply the `@register` decorator.
3. Done. The router can now dispatch to it. No trunk code changes needed.

## WhatsApp Integration (OpenClaw-style)

Link WhatsApp via QR code — no Meta Business API needed.

### 1. Configure allowlist

Add your phone number (E.164) to `.env`:

```bash
WHATSAPP_ALLOW_FROM=+15551234567,+8613800138000
WHATSAPP_BRIDGE_SECRET=your-random-secret  # Optional: for bridge auth
```

Or edit `my_agent_os/config/channels.yaml` → `whatsapp.allow_from`.

### 2. Start Agent OS

```bash
python run.py   # or: uvicorn my_agent_os.api_gateway.main:app --reload
```

### 3. Start WhatsApp Bridge

```bash
cd channels/whatsapp-bridge
npm install
AGENT_OS_URL=http://127.0.0.1:8000 \
AGENT_OS_SECRET=your-random-secret \
  npm start
```

Scan the QR code with WhatsApp. Message the linked number — the agent replies.

**Alternative auth**: Use `API_KEY_CHANNEL` as `X-API-Key` header instead of `AGENT_OS_SECRET`.

---

## Enterprise Features

| Feature | Implementation |
|---------|----------------|
| **Audit logging** | ISO 8601 timestamps, JSONL daily files in `memory_layer/data/audit/` |
| **DM/Group policies** | allowlist, pairing, open, disabled — via `channels.yaml` |
| **Retry** | LLM calls: 3 attempts, exponential backoff with jitter |
| **Session isolation** | Per-channel, per-user IDs (`whatsapp:+1555...`, `console:owner`) |
| **Rate limiting** | Sliding window per API key (configurable) |

---

## Customizing for a New Client

1. Duplicate `agent_core/prompts/system_prompts.yaml`.
2. Override the `preferences:` block with client-specific constraints.
3. Point `settings.py` to the new file. Ship it.
