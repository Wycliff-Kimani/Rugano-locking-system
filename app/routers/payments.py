from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from app.database import supabase
from datetime import datetime, timedelta
import json

router = APIRouter()

@router.post("/confirm")
async def citapay_webhook(request: Request):
    raw_body = await request.body()
    
    try:
        body = json.loads(raw_body)
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    
    # Temporary — log the full payload
    print("=== CITAPAY WEBHOOK PAYLOAD ===")
    print(json.dumps(body, indent=2))
    print("=== END PAYLOAD ===")
    
    return JSONResponse({"received": True})


@router.post("/validate")
async def mpesa_validate(request: Request):
    return JSONResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@router.post("/simulate")
async def simulate_payment(request: Request):
    body = await request.json()
    unit_number = body.get("unit_number", "").strip().upper()
    amount = body.get("amount", 0)

    if not unit_number or not amount:
        raise HTTPException(status_code=400, detail="unit_number and amount required")

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
    fake_ref = f"SIM{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    supabase.table("tenants").update({
        "access_expires_at": new_expiry.isoformat()
    }).eq("id", tenant["id"]).execute()

    supabase.table("payments").insert({
        "tenant_id": tenant["id"],
        "unit_id": unit["id"],
        "amount": float(amount),
        "days_granted": round(days_granted, 2),
        "mpesa_ref": fake_ref,
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