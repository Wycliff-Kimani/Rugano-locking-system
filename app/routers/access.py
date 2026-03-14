from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.database import supabase
from app.auth import verify_password
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ─── DOOR SIMULATOR PAGE ─────────────────────────────────────────────────────

@router.get("/simulator", response_class=HTMLResponse)
async def simulator_page(request: Request):
    units = supabase.table("units").select("*").eq("is_occupied", True).execute().data or []
    return templates.TemplateResponse("tenant/simulator.html", {
        "request": request,
        "units": units
    })


# ─── PIN ENTRY / ACCESS CHECK ────────────────────────────────────────────────

@router.post("/check")
async def check_access(request: Request):
    body = await request.json()
    unit_number = body.get("unit_number", "").strip().upper()
    pin = body.get("pin", "").strip()

    if not unit_number or not pin:
        return JSONResponse({
            "access": "denied",
            "reason": "Unit number and PIN are required."
        }, status_code=400)

    # Find unit
    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        return JSONResponse({
            "access": "denied",
            "reason": f"Unit {unit_number} not found."
        })

    unit = unit_result.data[0]

    # Find active tenant
    tenant_result = supabase.table("tenants").select("*").eq(
        "unit_id", unit["id"]
    ).eq("is_active", True).execute()

    if not tenant_result.data:
        return JSONResponse({
            "access": "denied",
            "reason": "No active tenant for this unit."
        })

    tenant = tenant_result.data[0]

    # Verify PIN
    pin_valid = verify_password(pin, tenant["pin"])
    if not pin_valid:
        # Log failed attempt
        supabase.table("access_logs").insert({
            "tenant_id": tenant["id"],
            "unit_id": unit["id"],
            "event_type": "denied",
            "triggered_by": "pin",
            "note": "Wrong PIN entered"
        }).execute()

        return JSONResponse({
            "access": "denied",
            "reason": "Incorrect PIN."
        })

    # Check access expiry
    now = datetime.utcnow()
    access_expires = tenant.get("access_expires_at")

    if not access_expires:
        # Log denied — never paid
        supabase.table("access_logs").insert({
            "tenant_id": tenant["id"],
            "unit_id": unit["id"],
            "event_type": "denied",
            "triggered_by": "pin",
            "note": "No payment on record"
        }).execute()

        return JSONResponse({
            "access": "denied",
            "reason": "No payment recorded. Please pay rent to gain access."
        })

    # Parse expiry
    try:
        expiry_dt = datetime.fromisoformat(access_expires.replace("Z", "+00:00"))
        expiry_naive = expiry_dt.replace(tzinfo=None)
    except Exception:
        return JSONResponse({
            "access": "denied",
            "reason": "Access data error. Contact landlord."
        })

    if now > expiry_naive:
        # Log expired
        supabase.table("access_logs").insert({
            "tenant_id": tenant["id"],
            "unit_id": unit["id"],
            "event_type": "denied",
            "triggered_by": "pin",
            "note": f"Access expired at {expiry_naive.strftime('%Y-%m-%d %H:%M')}"
        }).execute()

        return JSONResponse({
            "access": "denied",
            "reason": f"Access expired on {expiry_naive.strftime('%d %b %Y, %H:%M')}. Please pay rent."
        })

    # ACCESS GRANTED
    days_remaining = (expiry_naive - now).days
    hours_remaining = int((expiry_naive - now).total_seconds() / 3600)

    supabase.table("access_logs").insert({
        "tenant_id": tenant["id"],
        "unit_id": unit["id"],
        "event_type": "granted",
        "triggered_by": "pin",
        "note": f"Access granted. Expires {expiry_naive.strftime('%d %b %Y')}"
    }).execute()

    return JSONResponse({
        "access": "granted",
        "tenant_name": tenant["full_name"],
        "unit": unit_number,
        "expires": expiry_naive.strftime("%d %b %Y, %H:%M"),
        "days_remaining": days_remaining,
        "hours_remaining": hours_remaining,
        "reason": f"Welcome {tenant['full_name']}. Access valid until {expiry_naive.strftime('%d %b %Y')}."
    })


# ─── CHECK ACCESS STATUS (for dashboard) ─────────────────────────────────────

@router.get("/status/{unit_number}")
async def access_status(unit_number: str):
    unit_number = unit_number.strip().upper()

    unit_result = supabase.table("units").select("*").eq("unit_number", unit_number).execute()
    if not unit_result.data:
        return JSONResponse({"status": "unknown", "reason": "Unit not found"})

    unit = unit_result.data[0]
    tenant_result = supabase.table("tenants").select("*").eq(
        "unit_id", unit["id"]
    ).eq("is_active", True).execute()

    if not tenant_result.data:
        return JSONResponse({"status": "vacant", "reason": "No active tenant"})

    tenant = tenant_result.data[0]
    access_expires = tenant.get("access_expires_at")

    if not access_expires:
        return JSONResponse({"status": "locked", "reason": "No payment on record"})

    now = datetime.utcnow()
    try:
        expiry_dt = datetime.fromisoformat(access_expires.replace("Z", "+00:00"))
        expiry_naive = expiry_dt.replace(tzinfo=None)
    except Exception:
        return JSONResponse({"status": "error", "reason": "Data error"})

    if now > expiry_naive:
        return JSONResponse({
            "status": "locked",
            "reason": f"Expired on {expiry_naive.strftime('%d %b %Y')}"
        })

    days_remaining = (expiry_naive - now).days
    return JSONResponse({
        "status": "unlocked",
        "tenant": tenant["full_name"],
        "expires": expiry_naive.strftime("%d %b %Y"),
        "days_remaining": days_remaining
    })