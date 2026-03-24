"""
Billing Routes — Stripe integration for subscription management.

POST /billing/checkout           — create Stripe Checkout Session (returns URL)
POST /billing/portal             — create Stripe Customer Portal session
POST /billing/webhook            — receive Stripe webhook events
GET  /billing/plans              — return plan definitions (no auth required)
GET  /billing/status             — current subscription status (JWT required)

Configure via environment:
  STRIPE_SECRET_KEY    — sk_live_... or sk_test_...
  STRIPE_WEBHOOK_SECRET — whsec_...
  APP_BASE_URL         — https://yourdomain.com (for redirect URLs)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from my_agent_os.auth.jwt_auth import decode_token
from my_agent_os.auth.user_store import get_user_store

router = APIRouter(prefix="/billing", tags=["Billing"])

_STRIPE_KEY       = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET   = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_BASE_URL         = os.getenv("APP_BASE_URL", "http://localhost:8000")

PLANS = {
    "free": {
        "name":        "Free",
        "price_usd":   0,
        "rpm":         20,
        "memory_limit": 500,
        "features":    ["500 memories", "20 req/min", "All skills", "WhatsApp bridge"],
    },
    "pro": {
        "name":        "Pro",
        "price_usd":   19,
        "stripe_price_id": os.getenv("STRIPE_PRICE_PRO", ""),
        "rpm":         120,
        "memory_limit": 10_000,
        "features":    ["10,000 memories", "120 req/min", "Priority LLM routing", "Audit export"],
    },
    "enterprise": {
        "name":        "Enterprise",
        "price_usd":   99,
        "stripe_price_id": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
        "rpm":         600,
        "memory_limit": 100_000,
        "features":    ["Unlimited memories", "600 req/min", "Dedicated support", "SLA"],
    },
}


# ── Stripe HTTP helper (no SDK needed) ───────────────────────────

def _stripe_post(path: str, data: dict) -> dict:
    if not _STRIPE_KEY:
        raise HTTPException(503, "Stripe not configured. Set STRIPE_SECRET_KEY.")
    payload = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        f"https://api.stripe.com/v1/{path}",
        data=payload,
        headers={
            "Authorization": f"Bearer {_STRIPE_KEY}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _stripe_get(path: str) -> dict:
    if not _STRIPE_KEY:
        raise HTTPException(503, "Stripe not configured.")
    req = urllib.request.Request(
        f"https://api.stripe.com/v1/{path}",
        headers={"Authorization": f"Bearer {_STRIPE_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _get_jwt_user(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required.")
    try:
        payload = decode_token(authorization[7:].strip())
    except ValueError as e:
        raise HTTPException(401, str(e))
    store = get_user_store()
    user  = store.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(401, "User not found.")
    return user


# ── Routes ───────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans() -> dict[str, Any]:
    return {"plans": PLANS}


class CheckoutRequest(BaseModel):
    plan: str


@router.post("/checkout")
async def create_checkout(
    req: CheckoutRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    user       = _get_jwt_user(authorization)
    plan_info  = PLANS.get(req.plan)
    if not plan_info:
        raise HTTPException(400, f"Unknown plan: {req.plan}")
    if plan_info["price_usd"] == 0:
        raise HTTPException(400, "Free plan requires no checkout.")
    price_id = plan_info.get("stripe_price_id", "")
    if not price_id:
        raise HTTPException(503, f"Stripe price ID not configured for plan: {req.plan}")

    # Get or create Stripe customer
    customer_id = user.get("stripe_customer_id", "")
    if not customer_id:
        cust = _stripe_post("customers", {
            "email":    user["email"],
            "metadata[user_id]": user["id"],
        })
        customer_id = cust["id"]
        get_user_store().update_plan(user["id"], user["plan"], stripe_customer_id=customer_id)

    session = _stripe_post("checkout/sessions", {
        "customer":                customer_id,
        "mode":                    "subscription",
        "line_items[0][price]":    price_id,
        "line_items[0][quantity]": "1",
        "success_url":             f"{_BASE_URL}/?checkout=success&plan={req.plan}",
        "cancel_url":              f"{_BASE_URL}/?checkout=cancelled",
        "metadata[user_id]":       user["id"],
        "metadata[plan]":          req.plan,
    })
    return {"checkout_url": session["url"], "session_id": session["id"]}


@router.post("/portal")
async def create_portal(authorization: str | None = Header(None)) -> dict[str, Any]:
    user = _get_jwt_user(authorization)
    customer_id = user.get("stripe_customer_id", "")
    if not customer_id:
        raise HTTPException(400, "No Stripe customer linked. Subscribe first.")
    portal = _stripe_post("billing_portal/sessions", {
        "customer":   customer_id,
        "return_url": _BASE_URL,
    })
    return {"portal_url": portal["url"]}


@router.get("/status")
async def subscription_status(authorization: str | None = Header(None)) -> dict[str, Any]:
    user = _get_jwt_user(authorization)
    return {
        "plan":        user["plan"],
        "sub_status":  user.get("sub_status", "none"),
        "limits":      PLANS.get(user["plan"], PLANS["free"]),
    }


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    body = await request.body()
    sig  = request.headers.get("stripe-signature", "")

    if _WEBHOOK_SECRET:
        # Verify Stripe signature
        try:
            parts     = {p.split("=")[0]: p.split("=")[1] for p in sig.split(",")}
            ts        = parts.get("t", "0")
            signed    = f"{ts}.".encode() + body
            expected  = hmac.new(_WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
            received  = parts.get("v1", "")
            if not hmac.compare_digest(expected, received):
                raise HTTPException(400, "Invalid Stripe signature.")
        except (KeyError, IndexError):
            raise HTTPException(400, "Malformed Stripe-Signature header.")

    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON.")

    event_type = event.get("type", "")
    obj        = event.get("data", {}).get("object", {})

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        user_id = obj.get("metadata", {}).get("user_id", "")
        plan    = obj.get("metadata", {}).get("plan", "pro")
        status  = obj.get("status", "active")
        if user_id:
            get_user_store().update_plan(
                user_id,
                plan if status == "active" else "free",
                stripe_customer_id=obj.get("customer", ""),
                stripe_sub_id=obj.get("id", ""),
                sub_status=status,
            )

    elif event_type == "customer.subscription.deleted":
        user_id = obj.get("metadata", {}).get("user_id", "")
        if user_id:
            get_user_store().update_plan(user_id, "free", sub_status="cancelled")

    elif event_type == "checkout.session.completed":
        session    = obj
        user_id    = session.get("metadata", {}).get("user_id", "")
        plan       = session.get("metadata", {}).get("plan", "pro")
        cust_id    = session.get("customer", "")
        if user_id:
            get_user_store().update_plan(user_id, plan,
                stripe_customer_id=cust_id, sub_status="active")

    return {"received": True}
