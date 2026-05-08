"""Stripe billing for SkyCoach Pro subscriptions.

Public surface:
- create_checkout_session(user, success_url, cancel_url) → Stripe Checkout URL
- handle_webhook(payload, signature) → updates User.plan based on the event
- enforce_quota(user, db) → raises HTTPException 402 if free user is over quota

The Stripe SDK is imported lazily so the app boots without it for development.
Set STRIPE_SECRET_KEY + STRIPE_PRICE_ID + STRIPE_WEBHOOK_SECRET in production.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .db import Flight, User

log = logging.getLogger(__name__)

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
FREE_MONTHLY_ANALYSES = int(os.environ.get("SKYCOACH_FREE_MONTHLY_ANALYSES", "3"))


def is_pro(user: User) -> bool:
    return user.plan in ("pro", "flight_school")


def monthly_usage(user: User, db: Session) -> int:
    """Number of flights uploaded by `user` since the start of the current UTC month."""
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(Flight)
        .filter(Flight.user_id == user.id, Flight.created_at >= start)
        .count()
    )


def enforce_quota(user: User, db: Session) -> None:
    """Raise HTTP 402 if a free user has already used their monthly quota."""
    if is_pro(user):
        return
    used = monthly_usage(user, db)
    if used >= FREE_MONTHLY_ANALYSES:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Free-Plan: max. {FREE_MONTHLY_ANALYSES} Analysen pro Monat. "
            "Upgrade auf Pro für unbegrenzte Analysen.",
        )


def _stripe_or_404():
    """Lazy import — raises a clean 503 when Stripe isn't configured."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Stripe ist nicht konfiguriert.",
        )
    try:
        import stripe
    except ImportError as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Stripe-SDK nicht installiert: {e}",
        )
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def create_checkout_session(user: User, success_url: str, cancel_url: str) -> str:
    stripe = _stripe_or_404()
    customer_id = user.stripe_customer_id or None
    if not customer_id:
        customer = stripe.Customer.create(email=user.email, name=user.name or None)
        customer_id = customer["id"]
        user.stripe_customer_id = customer_id

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(user.id),
    )
    return session["url"]


def handle_webhook(payload: bytes, signature: str, db: Session) -> dict:
    stripe = _stripe_or_404()
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid webhook: {e}")
    else:
        # Unsigned mode — only acceptable in dev.
        import json

        event = json.loads(payload)

    etype = event.get("type")
    obj = event.get("data", {}).get("object", {})
    customer_id = obj.get("customer")

    if not customer_id:
        return {"received": True, "ignored": True}

    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
    if not user:
        log.warning("webhook for unknown customer: %s", customer_id)
        return {"received": True, "unknown_customer": True}

    if etype == "checkout.session.completed":
        user.plan = "pro"
        user.stripe_subscription_id = obj.get("subscription", "") or user.stripe_subscription_id
    elif etype == "customer.subscription.updated":
        status_str = obj.get("status")
        user.plan = "pro" if status_str in ("active", "trialing") else "free"
        if obj.get("current_period_end"):
            user.plan_renews_at = datetime.fromtimestamp(obj["current_period_end"])
    elif etype == "customer.subscription.deleted":
        user.plan = "free"
        user.stripe_subscription_id = ""
        user.plan_renews_at = None

    db.commit()
    return {"received": True, "applied": etype}
