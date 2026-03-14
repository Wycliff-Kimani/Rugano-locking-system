from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.routers import admin, tenants, payments, access
import os

app = FastAPI(
    title="Rugano Prepaid Door Lock System",
    description="Prepaid rent-based door access control for Kenyan rental properties",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Routers
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(tenants.router, prefix="/tenants", tags=["Tenants"])
app.include_router(payments.router, prefix="/api/mpesa", tags=["Payments"])
app.include_router(access.router, prefix="/door", tags=["Door Access"])

# Root redirect
@app.get("/")
async def root():
    return RedirectResponse(url="/admin/login")

@app.get("/health")
async def health():
    return {"status": "Rugano system online"}