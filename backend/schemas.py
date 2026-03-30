"""
schemas.py — Pydantic v2 models for request validation and response serialisation.

Pydantic v2 is used throughout.  ORM models are read via from_attributes=True
(replaces orm_mode from v1).
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# File schemas
# ---------------------------------------------------------------------------

class ColumnInfo(BaseModel):
    """Schema information for a single CSV column."""
    name: str
    dtype: str                    # human-readable label: "integer", "float", "string", etc.
    non_null_rate: float          # 0.0 – 1.0
    sample_values: list[str]      # up to 5 unique values, converted to strings


class CsvFileListItem(BaseModel):
    """Lightweight file representation used in the file list endpoint.
    Omits columns_info and preview_data to keep the response small."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_size: int
    encoding: str
    row_count: int
    column_count: int
    description: str | None
    uploaded_at: str


class CsvFileDetail(BaseModel):
    """Full file representation including schema and preview data."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_size: int
    encoding: str
    row_count: int
    column_count: int
    columns_info: list[ColumnInfo]
    preview_data: list[dict[str, Any]]
    description: str | None
    uploaded_at: str


class UploadError(BaseModel):
    """Describes a single file that failed validation during a batch upload."""
    filename: str
    error: str


class UploadResponse(BaseModel):
    """Response from POST /api/files/upload.
    Partial success is allowed: files that pass validation are returned in
    `uploaded`; files that fail are reported in `errors`."""
    uploaded: list[CsvFileDetail]
    errors: list[UploadError]


class DescriptionUpdateRequest(BaseModel):
    """Request body for PATCH /api/files/{id}/description."""
    description: str | None = None


# ---------------------------------------------------------------------------
# Chart spec schema (produced by the AI service, relayed to the frontend)
# ---------------------------------------------------------------------------

class ChartSpec(BaseModel):
    """Structured chart specification returned to the frontend as an SSE event.
    The frontend (Recharts) renders this directly without any server-side
    image generation.

    chart_type values: "bar" | "line" | "pie" | "scatter" | "heatmap"

    data layout by chart type:
      bar / line : [{"name": ..., "value": ...}, ...]  — x_key / y_key name the fields
      pie        : [{"name": ..., "value": ...}, ...]
      scatter    : [{"x": ..., "y": ...}, ...]
      heatmap    : [{"row": ..., "col": ..., "value": ...}, ...]
    """
    chart_type: str
    title: str
    data: list[dict[str, Any]]
    x_key: str | None = None
    y_key: str | None = None
    x_label: str | None = None
    y_label: str | None = None


# ---------------------------------------------------------------------------
# Chat schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Request body for POST /api/chat/stream."""
    message: str
    # IDs of the CSV files currently selected by the user
    file_ids: list[str]


class ConversationMessage(BaseModel):
    """A single conversation turn as stored in the DB."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    file_ids: list[str]
    created_at: str


class SuggestionsResponse(BaseModel):
    """Response from GET /api/chat/suggestions."""
    suggestions: list[str]
