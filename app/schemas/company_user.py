from typing import Optional

from pydantic import BaseModel, EmailStr


class CompanyUserCreate(BaseModel):
    """
    Used to create a role=USER account under one Company/tenant - either
    by that Company's own admin (POST /company/users) or by the Super
    Admin who owns that Customer (POST /customers/{id}/users). Role and
    customer_id are never taken from the client here; they're forced
    server-side (role=USER, customer_id=the tenant in question), so a
    caller can never grant themselves elevated access or drop a user into
    someone else's tenant.
    """
    full_name: str
    email: EmailStr
    password: str


class CompanyUserUpdate(BaseModel):
    full_name: Optional[str] = None
    is_active: Optional[bool] = None


class CompanyUserResponse(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    customer_id: Optional[int] = None
    is_active: bool

    class Config:
        from_attributes = True
