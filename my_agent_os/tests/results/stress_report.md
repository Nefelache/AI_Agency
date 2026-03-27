# Agent OS — Stress Test Report

**Generated:** 2026-03-24T07:44:21Z

**Config:** http://127.0.0.1:8000 | GET requests/phase: 800 | Workers: 16

## Summary

| Endpoint | Total | Success | Errors | Error % | RPS | P50 (ms) | P95 (ms) | P99 (ms) |
|----------|-------|---------|--------|---------|-----|----------|----------|----------|
| GET /health | 800 | 800 | 0 | 0.0% | 4942.89 | 2.72 | 5.04 | 6.81 |
| GET /billing/plans | 800 | 800 | 0 | 0.0% | 5215.36 | 2.68 | 4.75 | 5.98 |
| GET / | 800 | 800 | 0 | 0.0% | 2475.65 | 5.76 | 8.45 | 27.26 |
| POST /auth/register | 20 | 20 | 0 | 0.0% | 65.05 | 12.90 | 52.17 | 52.17 |

### Skipped (no load applied)

- **GET /memory/stats**: API_KEY_OWNER not set
- **GET /memory/list**: API_KEY_OWNER not set

## Interpretation

- **GET phases**: fixed total requests split across worker threads (bounded, reproducible).
- **POST /auth/register**: 20 sequential sign-ups (unique emails) to exercise SQLite user store.
- **Memory endpoints**: require `API_KEY_OWNER` in the environment matching server `.env`.