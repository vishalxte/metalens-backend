from typing import Optional
from datetime import datetime

from pydantic import BaseModel, EmailStr


class CustomerCreate(BaseModel):
    """
    Body for the Super Admin "create customer" form. A default Customer
    user is auto-created from `email` + `password` (see CustomerService),
    so this is the only signup path in the multi-tenant design — there is
    no public self-registration.
    """
    company_name: str
    address: Optional[str] = None
    mobile: Optional[str] = None
    email: EmailStr
    password: str


class CustomerUpdate(BaseModel):
    company_name: Optional[str] = None
    address: Optional[str] = None
    mobile: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerResponse(BaseModel):
    id: int
    company_name: str
    address: Optional[str] = None
    mobile: Optional[str] = None
    email: str
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
