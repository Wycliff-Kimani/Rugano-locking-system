from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from app.database import supabase
from app.models import MPesaCallback
from datetime import datetime, timedelta

router = APIRouter()

@router.post("/callback")
async def mpesa_callback(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Extract fields — Safaricom sends different structures
    # Handle both direct and nested callback formats
    try:
        trans_id = body.get("TransID") or body.get("transID", "")
        trans_amount = body.get("TransAmount") or body.get("transAmount", "0")
        bill_ref = body.get("BillRefNumber") or body.get("billRefNumber", "")
        trans_time = body.get("TransTime") or body.get("transTime", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Missing required fields")

    if not bill_ref:
        return JSONResponse({"ResultCode": 1, "ResultDesc": "Missing account reference"})

    # Normalize unit number — uppercase, strip spaces
    unit_number = bill_ref.strip().upper()

    # Find unit
    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        return JSONResponse({"ResultCode": 1, "ResultDesc": f"Unit {unit_number} not found"})

    unit = unit_result.data[0]

    # Find active tenant for this unit
    tenant_result = supabase.table("tenants").select("*").eq("unit_id", unit["id"]).eq("is_active", True).execute()
    if not tenant_result.data:
        return JSONResponse({"ResultCode": 1, "ResultDesc": "No active tenant for this unit"})

    tenant = tenant_result.data[0]

    # Calculate days granted
    try:
        amount = float(trans_amount)
    except ValueError:
        return JSONResponse({"ResultCode": 1, "ResultDesc": "Invalid amount"})

    monthly_rent = float(unit["monthly_rent"])
    daily_cost = monthly_rent / 30
    days_granted = amount / daily_cost

    # Calculate new expiry
    now = datetime.utcnow()
    current_expiry = tenant.get("access_expires_at")

    if current_expiry:
        try:
            # Parse existing expiry
            expiry_dt = datetime.fromisoformat(current_expiry.replace("Z", "+00:00"))
            # If already expired, extend from now
            base = max(now, expiry_dt.replace(tzinfo=None))
        except Exception:
            base = now
    else:
        base = now

    new_expiry = base + timedelta(days=days_granted)
    new_expiry_str = new_expiry.isoformat()

    # Update tenant access
    supabase.table("tenants").update({
        "access_expires_at": new_expiry_str
    }).eq("id", tenant["id"]).execute()

    # Log payment
    supabase.table("payments").insert({
        "tenant_id": tenant["id"],
        "unit_id": unit["id"],
        "amount": amount,
        "days_granted": round(days_granted, 2),
        "mpesa_ref": trans_id,
        "account_ref": unit_number,
    }).execute()

    return JSONResponse({
        "ResultCode": 0,
        "ResultDesc": f"Payment received. {round(days_granted, 1)} days access granted to {unit_number}."
    })


@router.get("/test-callback")
async def test_callback():
    """
    Test endpoint — simulates an M-Pesa payment without real M-Pesa.
    Use this to test the payment flow during development.
    GET /api/mpesa/test-callback?unit=A1&amount=1000
    """
    return {"message": "Use POST /api/mpesa/simulate to test payments"}


@router.post("/simulate")
async def simulate_payment(request: Request):
    """
    Simulate an M-Pesa payment for testing.
    Body: { "unit_number": "A1", "amount": 1000 }
    """
    body = await request.json()
    unit_number = body.get("unit_number", "").strip().upper()
    amount = body.get("amount", 0)

    if not unit_number or not amount:
        raise HTTPException(status_code=400, detail="unit_number and amount required")

    # Reuse callback logic by building a fake payload
    fake_payload = {
        "TransID": f"SIM{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "TransAmount": str(amount),
        "BillRefNumber": unit_number,
        "TransTime": datetime.utcnow().strftime("%Y%m%d%H%M%S")
    }

    # Process it through callback logic
    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found")

    unit = unit_result.data[0]
    tenant_result = supabase.table("tenants").select("*").eq("unit_id", unit["id"]).eq("is_active", True).execute()
    if not tenant_result.data:
        raise HTTPException(status_code=404, detail="No active tenant for this unit")

    tenant = tenant_result.data[0]
    monthly_rent = float(unit["monthly_rent"])
    daily_cost = monthly_rent / 30
    days_granted = float(amount) / daily_cost

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

    supabase.table("tenants").update({
        "access_expires_at": new_expiry.isoformat()
    }).eq("id", tenant["id"]).execute()

    supabase.table("payments").insert({
        "tenant_id": tenant["id"],
        "unit_id": unit["id"],
        "amount": float(amount),
        "days_granted": round(days_granted, 2),
        "mpesa_ref": fake_payload["TransID"],
        "account_ref": unit_number,
    }).execute()

    return {
        "status": "success",
        "unit": unit_number,
        "tenant": tenant["full_name"],
        "amount_paid": float(amount),
        "days_granted": round(days_granted, 2),
        "access_valid_until": new_expiry.isoformat()
    }