from fastapi import APIRouter
from fastapi import Depends

from app.api.dependencies.auth import (
    get_current_user
)

router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


@router.get("/me")
def current_user(
    user=Depends(get_current_user)
):

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "customer_id": user.customer_id
    }