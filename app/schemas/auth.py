from typing import Optional

from pydantic import BaseModel, EmailStr, model_validator

from app.models.user import Role


# Roles that must NOT have a customer_id - global/tenant-less accounts.
GLOBAL_ROLES = {Role.SUPER_ADMIN, Role.ADMIN}

# Roles that MUST have a customer_id - tenant-scoped accounts.
TENANT_ROLES = {Role.CUSTOMER, Role.USER}


class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str

    # Optional - defaults to CUSTOMER if omitted, matching the old
    # behavior. A Super Admin calling this can now explicitly ask for
    # SUPER_ADMIN / ADMIN / USER / CUSTOMER instead of always getting
    # CUSTOMER.
    role: str = Role.CUSTOMER

    # Required for CUSTOMER / USER (tenant-scoped roles). Must be None for
    # SUPER_ADMIN and ADMIN, which are both global/tenant-less roles - an
    # ADMIN is meant to operate across customers, the same as SUPER_ADMIN,
    # just with a narrower permission set (enforced separately wherever
    # ADMIN-only actions are added later).
    customer_id: Optional[int] = None

    @model_validator(mode="after")
    def validate_role_and_customer(self):
        valid_roles = GLOBAL_ROLES | TENANT_ROLES

        if self.role not in valid_roles:
            raise ValueError(
                "role must be one of " + str(sorted(valid_roles)) + ", got '" + str(self.role) + "'"
            )

        if self.role in GLOBAL_ROLES and self.customer_id is not None:
            raise ValueError(
                "role '" + str(self.role) + "' is global and must not have a customer_id"
            )

        if self.role in TENANT_ROLES and self.customer_id is None:
            raise ValueError(
                "customer_id is required for role '" + str(self.role) + "'"
            )

        return self


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """
    Returned by /auth/login. Includes role + customer_id (in addition to
    the JWT itself) so the frontend can immediately branch its UI
    (Super Admin console vs. Customer workspace) without decoding the
    token or making a follow-up /users/me call.
    """
    access_token: str
    token_type: str = "bearer"
    role: str
    customer_id: Optional[int] = None


class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str

class LoginResponse(BaseModel):
      message: str
      otp_required: bool   

class ResendOTPResponse(BaseModel):
      message: str

class ResendOTPRequest(BaseModel):
      email: EmailStr
      otp: str            