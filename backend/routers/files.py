"""
routers/files.py — HTTP handlers for CSV file management.

Endpoints:
  POST   /api/files/upload             Upload one or more CSV files
  GET    /api/files                    List all files (lightweight)
  GET    /api/files/{file_id}          Full file detail (schema + preview)
  PATCH  /api/files/{file_id}/description   Update description
  DELETE /api/files/{file_id}          Delete file from disk and DB
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from models import CsvFile
from schemas import (
    CsvFileDetail,
    CsvFileListItem,
    ColumnInfo,
    DescriptionUpdateRequest,
    UploadError,
    UploadResponse,
)
from services import csv_parser

router = APIRouter()

# Path to the storage directory — resolved relative to this file's location
STORAGE_DIR = os.path.join(os.path.dirname(__file__), "..", "storage")

# Per-file size limit: 50 MB
MAX_FILE_SIZE = 50 * 1024 * 1024

# Total batch size limit: 200 MB
MAX_TOTAL_SIZE = 200 * 1024 * 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_file_to_detail(record: CsvFile) -> CsvFileDetail:
    """Deserialise JSON TEXT columns and build a CsvFileDetail response."""
    columns_info = [ColumnInfo(**col) for col in json.loads(record.columns_info)]
    preview_data = json.loads(record.preview_data)
    return CsvFileDetail(
        id=record.id,
        filename=record.filename,
        file_size=record.file_size,
        encoding=record.encoding,
        row_count=record.row_count,
        column_count=record.column_count,
        columns_info=columns_info,
        preview_data=preview_data,
        description=record.description,
        uploaded_at=record.uploaded_at,
    )


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse)
def upload_files(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload one or more CSV files.

    Validation (applied per file before any disk write):
      - File extension must be .csv
      - Individual file size must not exceed 50 MB
      - Cumulative size of the batch must not exceed 200 MB

    Partial success is supported: files that fail validation are reported
    in the `errors` list while successfully processed files are in `uploaded`.
    """
    uploaded: list[CsvFileDetail] = []
    errors: list[UploadError] = []
    total_size = 0

    for upload in files:
        filename = upload.filename or "unknown.csv"

        # --- filename extension check ---
        if not filename.lower().endswith(".csv"):
            errors.append(UploadError(filename=filename, error="Only .csv files are accepted."))
            continue

        # --- read file contents ---
        raw_bytes = upload.file.read()
        file_size = len(raw_bytes)

        # --- per-file size check ---
        if file_size > MAX_FILE_SIZE:
            errors.append(
                UploadError(
                    filename=filename,
                    error=f"File size {file_size / 1024 / 1024:.1f} MB exceeds the 50 MB limit.",
                )
            )
            continue

        # --- cumulative size check ---
        total_size += file_size
        if total_size > MAX_TOTAL_SIZE:
            errors.append(
                UploadError(
                    filename=filename,
                    error="Batch total size exceeds the 200 MB limit. This file was skipped.",
                )
            )
            total_size -= file_size  # do not count this file toward the total
            continue

        # --- encoding detection and CSV parsing ---
        try:
            encoding = csv_parser.detect_encoding(raw_bytes)
            df = csv_parser.parse_csv(raw_bytes, encoding)
        except ValueError as exc:
            errors.append(UploadError(filename=filename, error=str(exc)))
            continue

        # --- write to disk ---
        file_id = str(uuid.uuid4())
        stored_name = f"{file_id}.csv"
        file_path = os.path.abspath(os.path.join(STORAGE_DIR, stored_name))

        with open(file_path, "wb") as f:
            f.write(raw_bytes)

        # --- extract schema and preview ---
        columns_info = csv_parser.extract_schema(df)
        preview_data = csv_parser.get_preview(df, n=10)

        # --- persist metadata to DB ---
        record = CsvFile(
            id=file_id,
            filename=filename,
            stored_name=stored_name,
            file_path=file_path,
            file_size=file_size,
            encoding=encoding,
            row_count=len(df),
            column_count=len(df.columns),
            columns_info=json.dumps(columns_info),
            preview_data=json.dumps(preview_data),
            description=None,
            uploaded_at=_now_utc(),
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        uploaded.append(_db_file_to_detail(record))

    return UploadResponse(uploaded=uploaded, errors=errors)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get("", response_model=list[CsvFileListItem])
def list_files(db: Session = Depends(get_db)):
    """
    Return all uploaded files as lightweight list items.
    columns_info and preview_data are excluded to keep the response small.
    """
    records = db.query(CsvFile).order_by(CsvFile.uploaded_at.desc()).all()
    return [
        CsvFileListItem(
            id=r.id,
            filename=r.filename,
            file_size=r.file_size,
            encoding=r.encoding,
            row_count=r.row_count,
            column_count=r.column_count,
            description=r.description,
            uploaded_at=r.uploaded_at,
        )
        for r in records
    ]


# ---------------------------------------------------------------------------
# GET /{file_id}
# ---------------------------------------------------------------------------

@router.get("/{file_id}", response_model=CsvFileDetail)
def get_file(file_id: str, db: Session = Depends(get_db)):
    """Return full metadata for a single file, including schema and preview."""
    record = db.query(CsvFile).filter(CsvFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found.")
    return _db_file_to_detail(record)


# ---------------------------------------------------------------------------
# PATCH /{file_id}/description
# ---------------------------------------------------------------------------

@router.patch("/{file_id}/description", response_model=CsvFileDetail)
def update_description(
    file_id: str,
    body: DescriptionUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Add or update the text description for a file.
    Pass `description: null` or `description: ""` to clear it.
    """
    record = db.query(CsvFile).filter(CsvFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found.")

    record.description = body.description or None
    db.commit()
    db.refresh(record)
    return _db_file_to_detail(record)


# ---------------------------------------------------------------------------
# DELETE /{file_id}
# ---------------------------------------------------------------------------

@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str, db: Session = Depends(get_db)):
    """
    Delete a file's metadata from the DB and its raw bytes from disk.

    If the physical file is already missing on disk (e.g. manual deletion),
    the DB row is still cleaned up and the endpoint succeeds.
    """
    record = db.query(CsvFile).filter(CsvFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found.")

    # Remove physical file (ignore if already missing)
    if os.path.exists(record.file_path):
        os.remove(record.file_path)

    db.delete(record)
    db.commit()
