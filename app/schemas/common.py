"""
Shared schema building blocks: pagination envelope and error response
shape, used consistently across every list endpoint (spec #37/#55).
"""
from typing import Generic, List, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int = Field(..., description="Total number of matching records across all pages")
    limit: int
    offset: int
    has_more: bool


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str | None = None


class PaginationParams(BaseModel):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)
