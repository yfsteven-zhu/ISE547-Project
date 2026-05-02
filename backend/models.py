"""
models.py — SQLAlchemy ORM models.

All timestamps are stored as ISO-8601 TEXT strings. SQLite has no native
datetime type; TEXT is the idiomatic choice and avoids timezone ambiguity.

JSON blobs (columns_info, preview_data, file_ids) are stored as TEXT and
serialised/deserialised with json.dumps / json.loads at the service layer.

Phase 2 extension points:
  - Add `project_id` (TEXT, FK) to CsvFile to associate files with projects.
  - Add `session_id` (TEXT, FK) to Conversation to group messages into named sessions.
"""

from sqlalchemy import Column, Integer, Text
from .database import Base


class CsvFile(Base):
    __tablename__ = "csv_files"

    id = Column(Text, primary_key=True, index=True)
    filename = Column(Text, nullable=False)
    stored_name = Column(Text, nullable=False, unique=True)
    file_path = Column(Text, nullable=False)
    file_size = Column(Integer, nullable=False)
    encoding = Column(Text, nullable=False)
    row_count = Column(Integer, nullable=False)
    column_count = Column(Integer, nullable=False)
    columns_info = Column(Text, nullable=False)
    preview_data = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    uploaded_at = Column(Text, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Text, primary_key=True, index=True)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    file_ids = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)