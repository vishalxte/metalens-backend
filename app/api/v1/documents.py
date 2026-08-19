import os
from typing import List

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.api.dependencies.auth import get_company_admin
from app.utils.file_validators import FileValidator
from app.core.exceptions import (
    DuplicateDocumentException,
    InvalidFileException
)
from app.schemas.document import DocumentDetail
from app.repositories.semantic_cache_repository import SemanticCacheRepository
from app.services.cache_service import cache_service
from app.core.logging import logger

from app.models.audit_log import AuditAction
from app.services.audit_service import audit_service

router = APIRouter(
    prefix="/documents",
    tags=["Documents"]
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ─────────────────────────────────────────
# Upload Single .md File (Customer users only — tenant-scoped)
# ─────────────────────────────────────────
@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    contents = await file.read()
    file_size = len(contents)

    logger.info(
        f"Upload request received: {file.filename} ({file_size} bytes) "
        f"from customer_id={current_user.customer_id}",
        extra={"event": "upload_request_received", "doc_filename": file.filename, "file_size": file_size}
    )

    if not FileValidator.validate_type(file.filename):
        logger.warning(f"Upload rejected — invalid file type: {file.filename}")
        raise InvalidFileException(
            detail=f"Only .md and .json files are allowed. Got: '{file.filename}'"
        )

    if not FileValidator.validate_size(file_size):
        logger.warning(f"Upload rejected — file too large: {file.filename} ({file_size} bytes)")
        raise InvalidFileException(
            detail=f"File too large: '{file.filename}'"
        )

    repo = DocumentRepository(db)

    # Duplicate-filename check is per-tenant, not global.
    if repo.get_by_filename(file.filename, current_user.customer_id):
        raise DuplicateDocumentException(
            detail=f"File already exists: '{file.filename}'"
        )

    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as buffer:
        buffer.write(contents)

    service = DocumentService(repo)

    return service.upload_document(
        filename=file.filename,
        file_path=file_path,
        user_id=current_user.id,
        customer_id=current_user.customer_id
    )


# ─────────────────────────────────────────
# Upload Multiple .md Files (Customer users only — tenant-scoped)
# ─────────────────────────────────────────
@router.post("/upload-multiple", status_code=207)
async def upload_multiple_documents(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = DocumentRepository(db)
    service = DocumentService(repo)

    results = []

    for file in files:
        try:
            contents = await file.read()
            file_size = len(contents)

            # Validate extension
            if not FileValidator.validate_type(file.filename):
                results.append({
                    "filename": file.filename,
                    "status": "failed",
                    "reason": f"Only .md and .json files are allowed. Got: '{file.filename}'"
                })
                continue

            # Validate size
            if not FileValidator.validate_size(file_size):
                results.append({
                    "filename": file.filename,
                    "status": "failed",
                    "reason": f"File too large: '{file.filename}'"
                })
                continue

            # Check duplicate (per-tenant)
            if repo.get_by_filename(file.filename, current_user.customer_id):
                results.append({
                    "filename": file.filename,
                    "status": "failed",
                    "reason": f"File already exists: '{file.filename}'"
                })
                continue

            # Save to disk
            file_path = os.path.join(UPLOAD_DIR, file.filename)
            with open(file_path, "wb") as buffer:
                buffer.write(contents)

            # Process and index
            result = service.upload_document(
                filename=file.filename,
                file_path=file_path,
                user_id=current_user.id,
                customer_id=current_user.customer_id
            )

            results.append({
                "filename": file.filename,
                "status": "success",
                "id": result["id"]
            })

        except Exception as e:
            results.append({
                "filename": file.filename,
                "status": "failed",
                "reason": str(e)
            })

    # Summary counts
    total = len(results)
    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = total - success_count

    return {
        "summary": {
            "total": total,
            "success": success_count,
            "failed": failed_count
        },
        "results": results
    }

# ─────────────────────────────────────────
# List Documents (Customer users only — their own tenant's documents)
# ─────────────────────────────────────────
@router.get("", status_code=200)
def list_documents(
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = DocumentRepository(db)
    documents = repo.get_all_for_customer(current_user.customer_id)

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
# View one Document's full content (Customer users only — own tenant)
# ─────────────────────────────────────────
@router.get("/{document_id}", response_model=DocumentDetail, status_code=200)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = DocumentRepository(db)
    document = repo.get_by_id_for_customer(document_id, current_user.customer_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


# ─────────────────────────────────────────
# Delete a File by ID (Customer users only — their own tenant's document)
# ─────────────────────────────────────────
@router.delete("/{document_id}", status_code=200)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    repo = DocumentRepository(db)
    service = DocumentService(repo)

    document = repo.get_by_id_for_customer(document_id, current_user.customer_id)

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found"
        )

    # We need the filename to return in the success message
    deleted_filename = document.filename

    # Call the new Service method that handles the cascades and cache clearing!
    success = service.delete_document(document_id, current_user.customer_id)

    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete document and clear caches."
        )

    return {
        "message": f"Document '{deleted_filename}' and its related caches were deleted successfully"
    }


# ─────────────────────────────────────────
# Clear this tenant's chat cache (Customer users only — own tenant)
# ─────────────────────────────────────────
@router.post("/clear-cache", status_code=200)
def clear_cache(
    db: Session = Depends(get_db),
    current_user=Depends(get_company_admin)
):
    """
    Wipes both cache tiers (Postgres exact_cache table + Postgres semantic
    cache) for the current tenant only. Needed because neither cache tier
    auto-invalidates when the retrieval/prompt CODE changes (only a new
    document upload triggers that) — during active development/testing,
    a previously-cached wrong answer for a question can keep being
    served indefinitely (exact cache: up to CACHE_TTL_SECONDS; semantic
    cache: until manually cleared) even after the underlying bug is
    fixed, unless this is called.
    """
    semantic_repo = SemanticCacheRepository(db)
    semantic_repo.delete_by_customer(current_user.customer_id)
    cache_service.clear_for_customer(current_user.customer_id)

    # Manual cache invalidation is an administrative action against the
    # knowledge base, so it belongs in the trail. Audited here rather
    # than in a service because this route has no service layer — same
    # documented exception as company_users.py.
    audit_service.log_knowledge_base_event(
        action=AuditAction.CACHE_CLEARED,
        details={
            "reason": "manual_clear_cache_endpoint",
            "customer_id": current_user.customer_id,
            "cache_tiers": ["exact", "semantic"]
        }
    )

    return {
        "message": "Cache cleared for your account. The next question (even if asked before) will run fresh retrieval + LLM."
    }
