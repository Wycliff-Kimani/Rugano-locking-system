from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.database import supabase
from app.auth import hash_password, verify_password, create_access_token, decode_token
from app.models import UserCreate, TenantCreate, UnitCreate, PINChange
from datetime import datetime
import uuid

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# ─── AUTH HELPER ────────────────────────────────────────────────────────────

def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)

def require_user(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})
    return user

# ─── LOGIN ───────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request})

@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.lower().strip()
    result = supabase.table("users").select("*").eq("email", email).execute()
    if not result.data:
        return templates.TemplateResponse("admin/login.html", {
            "request": request, "error": "Invalid email or password"
        })
    user = result.data[0]
    if not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse("admin/login.html", {
            "request": request, "error": "Invalid email or password"
        })
    token = create_access_token({"sub": user["id"], "email": user["email"], "role": user["role"]})
    response = RedirectResponse(url="/admin/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=3600)
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie("access_token")
    return response

# ─── DASHBOARD ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    units = supabase.table("units").select("*").execute().data or []
    tenants = supabase.table("tenants").select("*").eq("is_active", True).execute().data or []
    payments = supabase.table("payments").select("*").order("paid_at", desc=True).limit(5).execute().data or []
    logs = supabase.table("access_logs").select("*").order("logged_at", desc=True).limit(10).execute().data or []

    now = datetime.utcnow().isoformat()
    active_access = sum(1 for t in tenants if t.get("access_expires_at") and t["access_expires_at"] > now)
    expired_access = len(tenants) - active_access

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": user,
        "units": units,
        "tenants": tenants,
        "recent_payments": payments,
        "recent_logs": logs,
        "active_access": active_access,
        "expired_access": expired_access,
        "total_units": len(units),
        "total_tenants": len(tenants),
    })

# ─── TENANTS ─────────────────────────────────────────────────────────────────

@router.get("/tenants", response_class=HTMLResponse)
async def tenants_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    tenants = supabase.table("tenants").select("*, units(unit_number, monthly_rent)").execute().data or []
    units = supabase.table("units").select("*").eq("is_occupied", False).execute().data or []

    return templates.TemplateResponse("admin/tenants.html", {
        "request": request, "user": user, "tenants": tenants, "units": units
    })

@router.post("/tenants/add")
async def add_tenant(
    request: Request,
    full_name: str = Form(...),
    phone: str = Form(""),
    pin: str = Form(...),
    unit_id: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    hashed_pin = hash_password(pin)
    supabase.table("tenants").insert({
        "full_name": full_name,
        "phone": phone,
        "pin": hashed_pin,
        "unit_id": unit_id,
        "is_active": True
    }).execute()
    supabase.table("units").update({"is_occupied": True}).eq("id", unit_id).execute()
    return RedirectResponse(url="/admin/tenants", status_code=302)

@router.post("/tenants/deactivate")
async def deactivate_tenant(request: Request, tenant_id: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    tenant = supabase.table("tenants").select("unit_id").eq("id", tenant_id).execute().data
    if tenant:
        supabase.table("units").update({"is_occupied": False}).eq("id", tenant[0]["unit_id"]).execute()
    supabase.table("tenants").update({"is_active": False, "access_expires_at": None}).eq("id", tenant_id).execute()
    return RedirectResponse(url="/admin/tenants", status_code=302)

@router.post("/tenants/change-pin")
async def change_pin(request: Request, tenant_id: str = Form(...), new_pin: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    hashed_pin = hash_password(new_pin)
    supabase.table("tenants").update({"pin": hashed_pin}).eq("id", tenant_id).execute()
    return RedirectResponse(url="/admin/tenants", status_code=302)

# ─── UNITS ───────────────────────────────────────────────────────────────────

@router.get("/units", response_class=HTMLResponse)
async def units_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    units = supabase.table("units").select("*").execute().data or []
    property_data = supabase.table("properties").select("*").execute().data
    property_id = property_data[0]["id"] if property_data else None

    return templates.TemplateResponse("admin/units.html", {
        "request": request, "user": user, "units": units, "property_id": property_id
    })

@router.post("/units/add")
async def add_unit(request: Request, unit_number: str = Form(...), monthly_rent: float = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    property_data = supabase.table("properties").select("id").execute().data
    if not property_data:
        return RedirectResponse(url="/admin/units", status_code=302)

    supabase.table("units").insert({
        "property_id": property_data[0]["id"],
        "unit_number": unit_number.upper(),
        "monthly_rent": monthly_rent,
        "is_occupied": False
    }).execute()
    return RedirectResponse(url="/admin/units", status_code=302)

@router.post("/units/update-rent")
async def update_rent(request: Request, unit_id: str = Form(...), monthly_rent: float = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    supabase.table("units").update({"monthly_rent": monthly_rent}).eq("id", unit_id).execute()
    return RedirectResponse(url="/admin/units", status_code=302)

# ─── PAYMENTS ────────────────────────────────────────────────────────────────

@router.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    payments = supabase.table("payments").select(
        "*, tenants(full_name), units(unit_number)"
    ).order("paid_at", desc=True).execute().data or []

    return templates.TemplateResponse("admin/payments.html", {
        "request": request, "user": user, "payments": payments
    })

# ─── ACCESS LOGS ─────────────────────────────────────────────────────────────

@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    logs = supabase.table("access_logs").select(
        "*, tenants(full_name), units(unit_number)"
    ).order("logged_at", desc=True).limit(100).execute().data or []

    return templates.TemplateResponse("admin/logs.html", {
        "request": request, "user": user, "logs": logs
    })

# ─── MANUAL UNLOCK ───────────────────────────────────────────────────────────

@router.get("/unlock", response_class=HTMLResponse)
async def unlock_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    tenants = supabase.table("tenants").select(
        "*, units(unit_number)"
    ).eq("is_active", True).execute().data or []

    return templates.TemplateResponse("admin/unlock.html", {
        "request": request, "user": user, "tenants": tenants
    })

@router.post("/unlock")
async def manual_unlock(
    request: Request,
    tenant_id: str = Form(...),
    tenant_authorized: bool = Form(...),
    note: str = Form("")
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    if not tenant_authorized:
        return templates.TemplateResponse("admin/unlock.html", {
            "request": request, "user": user,
            "error": "Tenant authorization is required before unlocking."
        })

    tenant = supabase.table("tenants").select("unit_id").eq("id", tenant_id).execute().data
    unit_id = tenant[0]["unit_id"] if tenant else None

    supabase.table("access_logs").insert({
        "tenant_id": tenant_id,
        "unit_id": unit_id,
        "event_type": "manual_unlock",
        "triggered_by": "dashboard",
        "authorized_by": user["sub"],
        "note": note or "Manual unlock by landlord/caretaker"
    }).execute()

    return templates.TemplateResponse("admin/unlock.html", {
        "request": request, "user": user,
        "success": "Door unlocked successfully. Event logged."
    })

# ─── SETUP (first time) ──────────────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    existing = supabase.table("users").select("id").execute().data
    if existing:
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin/setup.html", {"request": request})

@router.post("/setup")
async def setup(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    property_name: str = Form(...),
    property_address: str = Form("")
):
    existing = supabase.table("users").select("id").execute().data
    if existing:
        return RedirectResponse(url="/admin/login", status_code=302)

    hashed = hash_password(password)
    user = supabase.table("users").insert({
        "full_name": full_name,
        "email": email,
        "password_hash": hashed,
        "role": "landlord"
    }).execute().data[0]

    supabase.table("properties").insert({
        "name": property_name,
        "address": property_address,
        "owner_id": user["id"]
    }).execute()

    return RedirectResponse(url="/admin/login?setup=success", status_code=302)