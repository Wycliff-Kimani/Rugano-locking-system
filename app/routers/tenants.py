from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from app.database import supabase
from datetime import datetime

router = APIRouter()


# ─── GET TENANT STATUS ───────────────────────────────────────────────────────

@router.get("/status/{unit_number}")
async def tenant_status(unit_number: str):
    unit_number = unit_number.strip().upper()

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
    access_expires = tenant.get("access_expires_at")
    now = datetime.utcnow()

    if not access_expires:
        return JSONResponse({
            "unit": unit_number,
            "tenant": tenant["full_name"],
            "status": "locked",
            "access_expires": None,
            "days_remaining": 0,
            "message": "No payment on record"
        })

    try:
        expiry_dt = datetime.fromisoformat(access_expires.replace("Z", "+00:00"))
        expiry_naive = expiry_dt.replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=500, detail="Error parsing access expiry")

    is_active = now < expiry_naive
    days_remaining = max(0, (expiry_naive - now).days)

    return JSONResponse({
        "unit": unit_number,
        "tenant": tenant["full_name"],
        "status": "unlocked" if is_active else "locked",
        "access_expires": expiry_naive.strftime("%d %b %Y, %H:%M"),
        "days_remaining": days_remaining,
        "message": f"Access {'active' if is_active else 'expired'}"
    })


# ─── GET PAYMENT HISTORY FOR A UNIT ─────────────────────────────────────────

@router.get("/payments/{unit_number}")
async def tenant_payments(unit_number: str):
    unit_number = unit_number.strip().upper()

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

    payments = supabase.table("payments").select("*").eq(
        "tenant_id", tenant["id"]
    ).order("paid_at", desc=True).execute().data or []

    return JSONResponse({
        "unit": unit_number,
        "tenant": tenant["full_name"],
        "total_payments": len(payments),
        "payments": [
            {
                "amount": p["amount"],
                "days_granted": p["days_granted"],
                "mpesa_ref": p["mpesa_ref"],
                "paid_at": p["paid_at"]
            }
            for p in payments
        ]
    })


# ─── GET ACCESS LOGS FOR A UNIT ──────────────────────────────────────────────

@router.get("/logs/{unit_number}")
async def tenant_logs(unit_number: str):
    unit_number = unit_number.strip().upper()

    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        raise HTTPException(status_code=404, detail=f"Unit {unit_number} not found")

    unit = unit_result.data[0]

    logs = supabase.table("access_logs").select("*").eq(
        "unit_id", unit["id"]
    ).order("logged_at", desc=True).limit(50).execute().data or []

    return JSONResponse({
        "unit": unit_number,
        "total_logs": len(logs),
        "logs": [
            {
                "event_type": l["event_type"],
                "triggered_by": l["triggered_by"],
                "note": l["note"],
                "logged_at": l["logged_at"]
            }
            for l in logs
        ]
    })