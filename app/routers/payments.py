from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from app.database import supabase
from datetime import datetime, timedelta
import json
import hmac
import hashlib
import httpx
import os
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── SHARED CREDIT LOGIC ─────────────────────────────────────────────────────

def credit_access(unit_number: str, amount: float, transaction_ref: str):
    unit_number = unit_number.strip().upper()

    # Duplicate check
    existing = supabase.table("payments").select("id").eq("mpesa_ref", transaction_ref).execute()
    if existing.data:
        return True, "already_processed"

    # Find unit
    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        return False, f"Unit {unit_number} not found"

    unit = unit_result.data[0]

    # Find active tenant
    tenant_result = supabase.table("tenants").select("*").eq(
        "unit_id", unit["id"]
    ).eq("is_active", True).execute()
    if not tenant_result.data:
        return False, "No active tenant for this unit"

    tenant = tenant_result.data[0]

    # Calculate days
    monthly_rent = float(unit["monthly_rent"])
    daily_cost = monthly_rent / 30
    days_granted = amount / daily_cost

    # Calculate expiry
    now = datetime.utcnow()
    current_expiry = tenant.get("access_expires_at")
    if current_expiry:
        try:
            expiry_dt = datetime.fromisoformat(current_expiry.replace("Z", "+00:00"))
            base = max(now, expiry_dt.replace(tzinfo=None))
        except Exception:
            base = now
    else:
        base = now

    new_expiry = base + timedelta(days=days_granted)

    # Update tenant access
    supabase.table("tenants").update({
        "access_expires_at": new_expiry.isoformat()
    }).eq("id", tenant["id"]).execute()

    # Log payment
    supabase.table("payments").insert({
        "tenant_id": tenant["id"],
        "unit_id": unit["id"],
        "amount": amount,
        "days_granted": round(days_granted, 2),
        "mpesa_ref": transaction_ref,
        "account_ref": unit_number,
    }).execute()

    # Log access event
    supabase.table("access_logs").insert({
        "tenant_id": tenant["id"],
        "unit_id": unit["id"],
        "event_type": "granted",
        "triggered_by": "citapay",
        "note": f"Payment KES {amount}. {round(days_granted, 1)} days granted. Ref: {transaction_ref}"
    }).execute()

    return True, {
        "tenant": tenant["full_name"],
        "unit": unit_number,
        "amount": amount,
        "days_granted": round(days_granted, 2),
        "access_valid_until": new_expiry.isoformat()
    }


# ─── CITAPAY WEBHOOK ─────────────────────────────────────────────────────────

@router.post("/confirm")
async def citapay_webhook(request: Request):
    raw_body = await request.body()

    # Verify signature
    webhook_secret = os.getenv("CITAPAY_WEBHOOK_SECRET", "")
    if webhook_secret:
        signature = request.headers.get("X-CitaPay-Signature", "")
        expected = hmac.new(
            webhook_secret.encode(),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning("Citapay webhook signature mismatch")
            return JSONResponse({"error": "Invalid signature"}, status_code=400)

    try:
        body = json.loads(raw_body)
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    event = body.get("event")

    # Only process completed payments
    if event != "payment.completed":
        return JSONResponse({"received": True, "note": f"Event {event} ignored"})

    data = body.get("data", {})
    transaction_ref = data.get("reference", "")
    amount = float(data.get("amount", 0))
    metadata = data.get("metadata", {})
    unit_number = metadata.get("unit_number", "").strip().upper()

    if not unit_number:
        logger.error(f"Citapay webhook missing unit_number in metadata. ref={transaction_ref}")
        return JSONResponse({"error": "unit_number missing from metadata"}, status_code=400)

    if amount <= 0:
        return JSONResponse({"error": "Invalid amount"}, status_code=400)

    success, result = credit_access(unit_number, amount, transaction_ref)

    if not success:
        logger.error(f"Credit access failed: {result}. ref={transaction_ref}")
        return JSONResponse({"error": result}, status_code=400)

    if result == "already_processed":
        return JSONResponse({"received": True, "note": "Duplicate ignored"})

    logger.info(f"Payment credited. unit={unit_number} amount={amount} ref={transaction_ref}")
    return JSONResponse({"received": True, "result": result})


# ─── INITIATE STK PUSH ───────────────────────────────────────────────────────

@router.post("/initiate")
async def initiate_payment(request: Request):
    body = await request.json()
    unit_number = body.get("unit_number", "").strip().upper()
    amount = body.get("amount", 0)
    phone = body.get("phone", "").strip()

    if not unit_number or not amount or not phone:
        raise HTTPException(status_code=400, detail="unit_number, amount and phone are required")

    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found")

    unit = unit_result.data[0]
    tenant_result = supabase.table("tenants").select("*").eq(
        "unit_id", unit["id"]
    ).eq("is_active", True).execute()
    if not tenant_result.data:
        raise HTTPException(status_code=404, detail="No active tenant for this unit")

    tenant = tenant_result.data[0]

    # Normalize phone
    if phone.startswith("0"):
        phone = "254" + phone[1:]

    citapay_key = os.getenv("CITAPAY_SECRET_KEY", "")
    if not citapay_key:
        raise HTTPException(status_code=500, detail="CITAPAY_SECRET_KEY not configured")

    citapay_base = os.getenv("CITAPAY_BASE_URL", "https://citapayapi.citatech.cloud/api/v1")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{citapay_base}/checkout/payments",
            headers={
                "Authorization": f"Bearer {citapay_key}",
                "Content-Type": "application/json"
            },
            json={
                "amount": float(amount),
                "paymentMethod": "MPESA",
                "phoneNumber": phone,
                "customerName": tenant["full_name"],
                "metadata": {
                    "unit_number": unit_number,
                    "tenant_id": tenant["id"]
                }
            },
            timeout=30
        )

    if response.status_code != 201:
        raise HTTPException(status_code=502, detail=f"Citapay error: {response.text}")

    result = response.json()
    return {
        "status": "pending",
        "message": f"STK push sent to {phone}. Tenant should check their phone.",
        "reference": result.get("reference"),
        "unit": unit_number,
        "tenant": tenant["full_name"],
        "amount": float(amount)
    }


# ─── VALIDATE ────────────────────────────────────────────────────────────────

@router.post("/validate")
async def mpesa_validate(request: Request):
    return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ─── SIMULATE (internal testing) ─────────────────────────────────────────────

@router.post("/simulate")
async def simulate_payment(request: Request):
    body = await request.json()
    unit_number = body.get("unit_number", "").strip().upper()
    amount = body.get("amount", 0)

    if not unit_number or not amount:
        raise HTTPException(status_code=400, detail="unit_number and amount required")

    fake_ref = f"SIM{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    success, result = credit_access(unit_number, float(amount), fake_ref)

    if not success:
        raise HTTPException(status_code=400, detail=result)

    return {"status": "success", **result}