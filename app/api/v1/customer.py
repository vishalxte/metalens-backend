import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.customer_repository import CustomerRepository
from app.repositories.user_repository import UserRepository
from app.repositories.document_repository import DocumentRepository
from app.services.customer_service import CustomerService
from app.services.document_service import DocumentService
from app.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse
)
from app.schemas.company_user import (
    CompanyUserCreate,
    CompanyUserUpdate,
    CompanyUserResponse
)
from app.schemas.document import DocumentDetail
from app.api.dependencies.auth import get_super_admin
from app.core.exceptions import (
    CustomerNotFoundException,
    DuplicateDocumentException,
    InvalidFileException
)
from app.core.security import hash_password
from app.models.user import User, Role
from app.models.user_session import SessionEndReason
from app.services.session_service import session_service
from app.utils.file_validators import FileValidator

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter(
    prefix="/customers",
    tags=["Customers"]
)


def _get_service(db: Session) -> CustomerService:
    return CustomerService(
        CustomerRepository(db),
        UserRepository(db)
    )


def _get_owned_customer_or_404(service: CustomerService, customer_id: int, current_user):
    """
    Every /customers/{customer_id}... route needs this same check: the
    Customer must exist AND have been created by *this* Super Admin.
    A Customer created by another Super Admin must 404 (not 403) so its
    existence isn't leaked either.
    """
    customer = service.get_by_id_for_creator(customer_id, current_user.id)

    if not customer:
        raise CustomerNotFoundException()

    return customer


# ─────────────────────────────────────────
# Create a Customer (Super Admin only). Auto-creates the customer's
# default user, per the PDF's onboarding flow.
# ─────────────────────────────────────────
@router.post("", response_model=CustomerResponse, status_code=201)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)

    customer, _default_user = service.create_customer(
        company_name=payload.company_name,
        address=payload.address,
        mobile=payload.mobile,
        email=payload.email,
        password=payload.password,
        created_by_user_id=current_user.id
    )

    return customer


# ─────────────────────────────────────────
# List Customers created by the current Super Admin only
# ─────────────────────────────────────────
@router.get("", response_model=list[CustomerResponse], status_code=200)
def list_customers(
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    return service.get_all_for_creator(current_user.id)


# ─────────────────────────────────────────
# Get One Customer (only if created by the current Super Admin)
# ─────────────────────────────────────────
@router.get("/{customer_id}", response_model=CustomerResponse, status_code=200)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    return _get_owned_customer_or_404(service, customer_id, current_user)


# ─────────────────────────────────────────
# Update a Customer (only if created by the current Super Admin)
# ─────────────────────────────────────────
@router.put("/{customer_id}", response_model=CustomerResponse, status_code=200)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    customer = _get_owned_customer_or_404(service, customer_id, current_user)

    return service.update(customer, **payload.model_dump(exclude_unset=True))


# ─────────────────────────────────────────
# Delete a Customer (only if created by the current Super Admin)
# ─────────────────────────────────────────
@router.delete("/{customer_id}", status_code=200)
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    customer = _get_owned_customer_or_404(service, customer_id, current_user)

    service.delete(customer)

    return {"message": f"Customer '{customer.company_name}' deleted successfully"}


# ─────────────────────────────────────────
# Super Admin: view documents uploaded by one of THEIR OWN customers.
# Per spec: Super Admin can view/delete Customer documents but never
# chats using them.
# ─────────────────────────────────────────
@router.get("/{customer_id}/documents", status_code=200)
def list_customer_documents(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    doc_repo = DocumentRepository(db)
    documents = doc_repo.get_all_for_customer(customer_id)

    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "created_at": doc.created_at
        }
        for doc in documents
    ]


# ─────────────────────────────────────────
# Super Admin: view one document's full content, for one of THEIR OWN
# customers.
# ─────────────────────────────────────────
@router.get("/{customer_id}/documents/{document_id}", response_model=DocumentDetail, status_code=200)
def get_customer_document(
    customer_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    doc_repo = DocumentRepository(db)
    document = doc_repo.get_by_id_for_customer(document_id, customer_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


# ─────────────────────────────────────────
# Super Admin: upload a document on behalf of one of THEIR OWN customers.
# Per the latest requirement, the Super Admin can manage a customer's
# documents end-to-end (upload + view + delete), same file-type/size
# rules as the Customer's own upload flow — just never chat with them.
# ─────────────────────────────────────────
@router.post("/{customer_id}/documents/upload", status_code=201)
async def upload_customer_document(
    customer_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    contents = await file.read()
    file_size = len(contents)

    if not FileValidator.validate_type(file.filename):
        raise InvalidFileException(
            detail=f"Only .md and .json files are allowed. Got: '{file.filename}'"
        )

    if not FileValidator.validate_size(file_size):
        raise InvalidFileException(
            detail=f"File too large: '{file.filename}'"
        )

    doc_repo = DocumentRepository(db)

    # Duplicate-filename check is per-tenant, not global.
    if doc_repo.get_by_filename(file.filename, customer_id):
        raise DuplicateDocumentException(
            detail=f"File already exists: '{file.filename}'"
        )

    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as buffer:
        buffer.write(contents)

    doc_service = DocumentService(doc_repo)

    return doc_service.upload_document(
        filename=file.filename,
        file_path=file_path,
        user_id=current_user.id,
        customer_id=customer_id
    )


# ─────────────────────────────────────────
# Super Admin: delete a document belonging to one of THEIR OWN customers.
# ─────────────────────────────────────────
@router.delete("/{customer_id}/documents/{document_id}", status_code=200)
def delete_customer_document(
    customer_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    doc_repo = DocumentRepository(db)
    document = doc_repo.get_by_id_for_customer(document_id, customer_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    deleted_filename = document.filename
    doc_service = DocumentService(doc_repo)
    success = doc_service.delete_document(document_id, customer_id)

    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete document and clear caches."
        )

    return {
        "message": f"Document '{deleted_filename}' and its related caches were deleted successfully"
    }


# ─────────────────────────────────────────
# Super Admin: create a User for one of THEIR OWN customers.
# ─────────────────────────────────────────
@router.post("/{customer_id}/users", response_model=CompanyUserResponse, status_code=201)
def create_customer_user(
    customer_id: int,
    payload: CompanyUserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    user_repo = UserRepository(db)

    if user_repo.get_by_email(payload.email):
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    user = User(
        full_name=payload.full_name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=Role.USER,
        customer_id=customer_id
    )

    return user_repo.create(user)


# ─────────────────────────────────────────
# Super Admin: list Users belonging to one of THEIR OWN customers.
# ─────────────────────────────────────────
@router.get("/{customer_id}/users", response_model=list[CompanyUserResponse], status_code=200)
def list_customer_users(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    user_repo = UserRepository(db)
    return user_repo.get_all_for_customer(customer_id)


# ─────────────────────────────────────────
# Super Admin: update a User belonging to one of THEIR OWN customers.
# ─────────────────────────────────────────
@router.put("/{customer_id}/users/{user_id}", response_model=CompanyUserResponse, status_code=200)
def update_customer_user(
    customer_id: int,
    user_id: int,
    payload: CompanyUserUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    user_repo = UserRepository(db)
    user = user_repo.get_by_id_for_customer(user_id, customer_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes = payload.model_dump(exclude_unset=True)
    previous_is_active = user.is_active

    updated = user_repo.update(user, **changes)

    # FORCE LOGOUT on deactivation. This is the Super Admin's path to
    # the same action /company/users/{id} exposes to a company admin —
    # both must terminate live sessions, or the deactivated user keeps
    # working on an already-issued token until it expires.
    if (
        "is_active" in changes
        and not changes["is_active"]
        and previous_is_active
    ):
        session_service.end_all_for_user(
            updated.id,
            reason=SessionEndReason.ACCOUNT_DISABLED,
            revoked_by_user_id=current_user.id
        )

    # Response unchanged — same object user_repo.update() returned before.
    return updated


# ─────────────────────────────────────────
# Super Admin: delete a User belonging to one of THEIR OWN customers.
# ─────────────────────────────────────────
@router.delete("/{customer_id}/users/{user_id}", status_code=200)
def delete_customer_user(
    customer_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_super_admin)
):
    service = _get_service(db)
    _get_owned_customer_or_404(service, customer_id, current_user)

    user_repo = UserRepository(db)
    user = user_repo.get_by_id_for_customer(user_id, customer_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    target_id = user.id

    user_repo.delete(user)

    # Soft delete == is_active=False, so the same reasoning as the
    # deactivate branch applies: end live sessions so the removal takes
    # effect immediately rather than whenever the token happens to expire.
    session_service.end_all_for_user(
        target_id,
        reason=SessionEndReason.ACCOUNT_DISABLED,
        revoked_by_user_id=current_user.id
    )

    return {"message": f"User '{user.email}' deleted successfully"}
