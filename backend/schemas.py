"""
schemas.py — Pydantic v2 models for request validation and response serialisation.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict


class ColumnInfo(BaseModel):
    name: str
    dtype: str
    non_null_rate: float
    sample_values: list[str]


class CsvFileListItem(BaseModel):
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
    filename: str
    error: str


class UploadResponse(BaseModel):
    uploaded: list[CsvFileDetail]
    errors: list[UploadError]


class DescriptionUpdateRequest(BaseModel):
    description: str | None = None


class ChartSpec(BaseModel):
    chart_type: str
    title: str
    data: list[dict[str, Any]]
    x_key: str | None = None
    y_key: str | None = None
    x_label: str | None = None
    y_label: str | None = None


class ChatRequest(BaseModel):
    message: str
    file_ids: list[str] = []
    prompt_id: str = "baseline"
    model_id: str = "nemotron"


class ConversationMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    file_ids: list[str]
    created_at: str


class SuggestionsResponse(BaseModel):
    suggestions: list[str]


class PromptInfo(BaseModel):
    id: str
    name: str
    description: str


class PromptListResponse(BaseModel):
    prompts: list[PromptInfo]


class ModelInfo(BaseModel):
    id: str
    name: str
    description: str


class ModelListResponse(BaseModel):
    models: list[ModelInfo]


# =========================
# Evaluation schemas
# =========================

class EvaluationRunRequest(BaseModel):
    file_ids: list[str]
    questions: list[str] = []
    model_ids: list[str] = ["nemotron", "elephant", "arcee_trinity", "gpt_oss"]
    prompt_ids: list[str] = ["baseline", "COT", "Action-Oriented"]


class EvaluationScoreRow(BaseModel):
    batch: int
    model: str
    prompt: str
    DF: float
    ACTN: float
    EXPL: float
    answer: str | None = None


class EvaluationBatchTable(BaseModel):
    batch: int
    rows: list[dict[str, Any]]


class EvaluationRunResponse(BaseModel):
    raw_scores: list[EvaluationScoreRow]
    batch_tables: list[EvaluationBatchTable]
    final_table: list[dict[str, Any]]