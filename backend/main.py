"""
main.py — FastAPI application factory.

Responsibilities:
  - Create the FastAPI instance.
  - Configure CORS for the Vite dev server (localhost:5173).
  - On startup: initialise the SQLite schema, ensure storage/ exists,
    and clear all data from the previous session (session-based storage).
  - Register all routers under the /api prefix.
"""

import glob
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before importing anything that reads environment variables
load_dotenv()

from database import Base, engine, SessionLocal  # noqa: E402 — must come after load_dotenv
from models import CsvFile, Conversation          # noqa: E402
from routers import files, chat                   # noqa: E402


# ---------------------------------------------------------------------------
# Storage directory
# ---------------------------------------------------------------------------

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage")


# ---------------------------------------------------------------------------
# Application lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all SQLite tables if they do not yet exist
    Base.metadata.create_all(bind=engine)

    # Ensure the CSV storage directory exists
    os.makedirs(STORAGE_DIR, exist_ok=True)

    # --- Session-based cleanup: remove all data from the previous run ---
    # Phase 1 files are scoped to the current server session only.
    with SessionLocal() as db:
        db.query(Conversation).delete()
        db.query(CsvFile).delete()
        db.commit()

    # Delete all physical CSV files from the storage directory
    for file_path in glob.glob(os.path.join(STORAGE_DIR, "*.csv")):
        try:
            os.remove(file_path)
        except OSError:
            pass  # Ignore files already missing

    yield
    # No teardown required for Phase 1


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Chat with Your Data",
    description="Upload CSV files and analyse them with natural language.",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow requests from the Vite dev server and (optionally) production build
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    # Allow all headers including Cache-Control, required for SSE preflight
    allow_headers=["*"],
    expose_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])


@app.get("/api/health")
def health_check():
    """Simple liveness probe."""
    return {"status": "ok"}
