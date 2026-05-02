from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CsvFile
from ..schemas import (
    CsvFileDetail,
    CsvFileListItem,
    ColumnInfo,
    DescriptionUpdateRequest,
    UploadError,
    UploadResponse,
)
from ..services import csv_parser

router = APIRouter()

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "..", "storage")

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_TOTAL_SIZE = 200 * 1024 * 1024


def _db_file_to_detail(record: CsvFile) -> CsvFileDetail:
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


@router.post("/upload", response_model=UploadResponse)
def upload_files(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    uploaded: list[CsvFileDetail] = []
    errors: list[UploadError] = []
    total_size = 0

    for upload in files:
        filename = upload.filename or "unknown.csv"

        if not filename.lower().endswith(".csv"):
            errors.append(
                UploadError(filename=filename, error="Only .csv files are accepted.")
            )
            continue

        raw_bytes = upload.file.read()
        file_size = len(raw_bytes)

        if file_size > MAX_FILE_SIZE:
            errors.append(
                UploadError(
                    filename=filename,
                    error=f"File size {file_size / 1024 / 1024:.1f} MB exceeds the 50 MB limit.",
                )
            )
            continue

        total_size += file_size
        if total_size > MAX_TOTAL_SIZE:
            errors.append(
                UploadError(
                    filename=filename,
                    error="Batch total size exceeds the 200 MB limit. This file was skipped.",
                )
            )
            total_size -= file_size
            continue

        try:
            encoding = csv_parser.detect_encoding(raw_bytes)
            df = csv_parser.parse_csv(raw_bytes, encoding)
        except ValueError as exc:
            errors.append(UploadError(filename=filename, error=str(exc)))
            continue

        file_id = str(uuid.uuid4())
        stored_name = f"{file_id}.csv"
        file_path = os.path.abspath(os.path.join(STORAGE_DIR, stored_name))

        os.makedirs(os.path.abspath(STORAGE_DIR), exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(raw_bytes)

        columns_info = csv_parser.extract_schema(df)
        preview_data = csv_parser.get_preview(df, n=10)

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


@router.get("", response_model=list[CsvFileListItem])
def list_files(db: Session = Depends(get_db)):
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


@router.get("/{file_id}", response_model=CsvFileDetail)
def get_file(file_id: str, db: Session = Depends(get_db)):
    record = db.query(CsvFile).filter(CsvFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found.")
    return _db_file_to_detail(record)


@router.patch("/{file_id}/description", response_model=CsvFileDetail)
def update_description(
    file_id: str,
    body: DescriptionUpdateRequest,
    db: Session = Depends(get_db),
):
    record = db.query(CsvFile).filter(CsvFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found.")

    record.description = body.description or None
    db.commit()
    db.refresh(record)
    return _db_file_to_detail(record)


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str, db: Session = Depends(get_db)):
    record = db.query(CsvFile).filter(CsvFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found.")

    if os.path.exists(record.file_path):
        os.remove(record.file_path)

    db.delete(record)
    db.commit()