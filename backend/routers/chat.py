"""
routers/chat.py — HTTP handlers for the conversation and AI analysis endpoints.

Endpoints:
  POST   /api/chat/stream      Stream an AI response (SSE)
  GET    /api/chat/history     Fetch full conversation history
  DELETE /api/chat/history     Clear all conversation messages
  GET    /api/chat/suggestions Template-based question suggestions (no AI call)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import Conversation, CsvFile
from schemas import ChatRequest, ConversationMessage, SuggestionsResponse
from services import ai_service, csv_parser
from services.code_executor import load_dataframes

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_message(record: Conversation) -> ConversationMessage:
    """Convert a DB Conversation row to a ConversationMessage schema."""
    file_ids = json.loads(record.file_ids) if record.file_ids else []
    return ConversationMessage(
        id=record.id,
        role=record.role,
        content=record.content,
        file_ids=file_ids,
        created_at=record.created_at,
    )


def _file_record_to_context(record: CsvFile) -> dict:
    """
    Build the file context dict expected by ai_service.build_system_prompt().
    Deserialises the JSON TEXT columns stored in the DB.
    """
    return {
        "filename": record.filename,
        "description": record.description,
        "row_count": record.row_count,
        "column_count": record.column_count,
        "encoding": record.encoding,
        "columns_info": json.loads(record.columns_info),
        "preview_data": json.loads(record.preview_data),
    }


# ---------------------------------------------------------------------------
# POST /stream
# ---------------------------------------------------------------------------

@router.post("/stream")
def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
    """
    Accept the user's message and selected file IDs, then stream an AI
    response via Server-Sent Events.

    Flow:
      1. Validate that all requested file_ids exist in the DB.
      2. Persist the user message to the conversations table.
      3. Fetch file metadata for context assembly.
      4. Pre-load DataFrames for potential code execution (feature 3.4).
      5. Fetch conversation history for multi-turn context.
      6. Return a StreamingResponse that yields SSE events from ai_service.

    Persistence note:
      The StreamingResponse generator outlives the request-scoped `db`
      session.  A fresh SessionLocal() is opened inside the generator to
      persist the assistant message after the stream completes.
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # --- validate file IDs ---
    if request.file_ids:
        records = (
            db.query(CsvFile)
            .filter(CsvFile.id.in_(request.file_ids))
            .all()
        )
        found_ids = {r.id for r in records}
        missing = [fid for fid in request.file_ids if fid not in found_ids]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"File(s) not found: {', '.join(missing)}",
            )
    else:
        records = []

    # --- persist user message (before streaming) ---
    user_msg_id = str(uuid.uuid4())
    user_msg = Conversation(
        id=user_msg_id,
        role="user",
        content=request.message,
        file_ids=json.dumps(request.file_ids),
        created_at=_now_utc(),
    )
    db.add(user_msg)
    db.commit()

    # --- build file context for the AI system prompt ---
    files_context = [_file_record_to_context(r) for r in records]

    # --- pre-load DataFrames for the code executor ---
    # Done here (in the request scope) so that DB reads are within the session.
    dataframes = load_dataframes(request.file_ids, db) if request.file_ids else {}

    # --- fetch conversation history (oldest-first, excluding current message) ---
    history_rows = (
        db.query(Conversation)
        .filter(Conversation.id != user_msg_id)
        .order_by(Conversation.created_at.asc())
        .all()
    )
    history = [{"role": r.role, "content": r.content} for r in history_rows]

    # Capture references needed inside the generator
    file_ids_snapshot = list(request.file_ids)
    message_text = request.message

    # --- generator that yields SSE chunks and persists the assistant reply ---
    def generate():
        assistant_parts: list[str] = []

        for chunk in ai_service.stream_chat_response(
            message=message_text,
            history=history,
            files_context=files_context,
            dataframes=dataframes,
        ):
            # Accumulate text_delta content to build the full assistant message
            try:
                event_data = json.loads(chunk.removeprefix("data: ").strip())
                if event_data.get("type") == "text_delta":
                    assistant_parts.append(event_data.get("content", ""))
            except (json.JSONDecodeError, AttributeError):
                pass

            yield chunk

        # --- persist the assistant message after streaming completes ---
        # Use a fresh session because the request-scoped `db` is already closed.
        full_response = "".join(assistant_parts)
        if full_response:
            with SessionLocal() as post_db:
                assistant_msg = Conversation(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    content=full_response,
                    file_ids=json.dumps(file_ids_snapshot),
                    created_at=_now_utc(),
                )
                post_db.add(assistant_msg)
                post_db.commit()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable nginx buffering if the server is placed behind a proxy
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /history
# ---------------------------------------------------------------------------

@router.get("/history", response_model=list[ConversationMessage])
def get_history(db: Session = Depends(get_db)):
    """
    Return the full conversation history ordered oldest-first.
    Used by the frontend to restore the chat view on page load.
    """
    rows = db.query(Conversation).order_by(Conversation.created_at.asc()).all()
    return [_row_to_message(r) for r in rows]


# ---------------------------------------------------------------------------
# DELETE /history
# ---------------------------------------------------------------------------

@router.delete("/history", status_code=204)
def clear_history(db: Session = Depends(get_db)):
    """
    Delete all conversation messages.
    Uploaded files are not affected.
    """
    db.query(Conversation).delete()
    db.commit()


# ---------------------------------------------------------------------------
# GET /suggestions
# ---------------------------------------------------------------------------

@router.get("/suggestions", response_model=SuggestionsResponse)
def get_suggestions(file_ids: str = "", db: Session = Depends(get_db)):
    """
    Return template-based question suggestions derived from the selected
    files' column names and types.  No Claude API call is made.

    Query parameter:
      file_ids — comma-separated list of CsvFile IDs
                 e.g. ?file_ids=abc123,def456
    """
    ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]

    if not ids:
        return SuggestionsResponse(suggestions=[
            "Upload a CSV file to get personalised suggestions.",
        ])

    records = db.query(CsvFile).filter(CsvFile.id.in_(ids)).all()

    files_metadata = [
        {
            "filename": r.filename,
            "columns_info": json.loads(r.columns_info),
        }
        for r in records
    ]

    suggestions = csv_parser.generate_suggestions(files_metadata)
    return SuggestionsResponse(suggestions=suggestions)
