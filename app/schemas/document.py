from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):

    id: int
    filename: str
    file_type: str
    customer_id: int
    created_by: int | None = None

    class Config:
        from_attributes = True


class DocumentDetail(BaseModel):
    """
    Full document payload for the "View" action — includes extracted_text
    so the frontend can render the actual content (in a modal, a new tab,
    wherever) without re-uploading or re-parsing anything.
    """
    id: int
    filename: str
    file_type: str
    status: str
    extracted_text: str | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True