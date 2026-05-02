import glob
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

from .database import Base, engine, SessionLocal
from .models import CsvFile, Conversation
from .routers import files, chat

STORAGE_DIR = os.path.join(BASE_DIR, "storage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    os.makedirs(STORAGE_DIR, exist_ok=True)

    with SessionLocal() as db:
        db.query(Conversation).delete()
        db.query(CsvFile).delete()
        db.commit()

    for file_path in glob.glob(os.path.join(STORAGE_DIR, "*.csv")):
        try:
            os.remove(file_path)
        except OSError:
            pass

    yield


app = FastAPI(
    title="Chat with Your Data",
    description="Upload CSV files and analyse them with natural language.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://your-frontend-name.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])


@app.get("/api/health")
def health_check():
    return {"status": "ok"}