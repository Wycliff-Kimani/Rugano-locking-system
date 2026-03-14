from pydantic import BaseModel
from typing import Optional
from datetime import datetime

# AUTH
class UserLogin(BaseModel):
    email: str
    password: str

class UserCreate(BaseModel):
    full_name: str
    email: str
    password: str
    role: str = "caretaker"

# PROPERTY
class PropertyCreate(BaseModel):
    name: str
    address: Optional[str] = None

# UNIT
class UnitCreate(BaseModel):
    unit_number: str
    monthly_rent: float

class UnitUpdate(BaseModel):
    monthly_rent: float

# TENANT
class TenantCreate(BaseModel):
    full_name: str
    phone: Optional[str] = None
    pin: str
    unit_id: str

class TenantUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None

class PINChange(BaseModel):
    tenant_id: str
    new_pin: str

# DOOR ACCESS
class DoorAccessRequest(BaseModel):
    unit_number: str
    pin: str

# MANUAL UNLOCK
class ManualUnlockRequest(BaseModel):
    unit_id: str
    tenant_authorized: bool
    note: Optional[str] = None

# MPESA CALLBACK
class MPesaCallback(BaseModel):
    TransactionType: Optional[str] = None
    TransID: Optional[str] = None
    TransTime: Optional[str] = None
    TransAmount: Optional[str] = None
    BusinessShortCode: Optional[str] = None
    BillRefNumber: Optional[str] = None
    InvoiceNumber: Optional[str] = None
    OrgAccountBalance: Optional[str] = None
    ThirdPartyTransID: Optional[str] = None
    MSISDN: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None