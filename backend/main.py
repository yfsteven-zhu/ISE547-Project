"""
main.py — FastAPI application factory.

Responsibilities:
  - Create the FastAPI instance.
  - Configure CORS for the Vite dev server (localhost:5173).
  - On startup: initialise the SQLite schema and ensure storage/ exists.
  - Register all routers under the /api prefix.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before importing anything that reads environment variables
load_dotenv()

from database import Base, engine  # noqa: E402 — must come after load_dotenv
from routers import files, chat    # noqa: E402


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

    yield
    # No teardown required for Phase 1


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Chat with Your Data",
    description="Upload CSV files and analyse them with natural language powered by Claude.",
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
