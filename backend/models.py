"""
models.py — SQLAlchemy ORM models.

All timestamps are stored as ISO-8601 TEXT strings.  SQLite has no native
datetime type; TEXT is the idiomatic choice and avoids timezone ambiguity.

JSON blobs (columns_info, preview_data, file_ids) are stored as TEXT and
serialised/deserialised with json.dumps / json.loads at the service layer.

Phase 2 extension points:
  - Add `project_id` (TEXT, FK) to CsvFile to associate files with projects.
  - Add `session_id` (TEXT, FK) to Conversation to group messages into named sessions.
"""

from sqlalchemy import Column, Integer, Text
from database import Base


class CsvFile(Base):
    __tablename__ = "csv_files"

    # Unique identifier (UUID4 string)
    id = Column(Text, primary_key=True, index=True)

    # Original filename as provided by the user
    filename = Column(Text, nullable=False)

    # UUID-based name used on disk (prevents collisions when two uploads share the same filename)
    stored_name = Column(Text, nullable=False, unique=True)

    # Absolute path to the raw CSV on disk (under backend/storage/)
    file_path = Column(Text, nullable=False)

    # File size in bytes
    file_size = Column(Integer, nullable=False)

    # Detected character encoding, e.g. "utf-8", "gb18030"
    encoding = Column(Text, nullable=False)

    row_count = Column(Integer, nullable=False)
    column_count = Column(Integer, nullable=False)

    # JSON TEXT: list of {name, dtype, non_null_rate, sample_values} dicts
    columns_info = Column(Text, nullable=False)

    # JSON TEXT: list of row dicts (first 10 rows), NaN replaced with null
    preview_data = Column(Text, nullable=False)

    # Optional user-provided description to help the AI understand the file's context
    description = Column(Text, nullable=True)

    # ISO-8601 UTC timestamp, e.g. "2026-03-30T08:00:00Z"
    uploaded_at = Column(Text, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    # Unique identifier (UUID4 string)
    id = Column(Text, primary_key=True, index=True)

    # "user" or "assistant"
    role = Column(Text, nullable=False)

    # Message content; assistant messages are Markdown
    content = Column(Text, nullable=False)

    # JSON TEXT: list of CsvFile IDs that were selected when this message was sent
    # Stored as a snapshot so conversation history is self-contained
    file_ids = Column(Text, nullable=True)

    # ISO-8601 UTC timestamp
    created_at = Column(Text, nullable=False)
