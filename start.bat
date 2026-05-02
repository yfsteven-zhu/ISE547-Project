@echo off
REM One-click launcher for the Chat-with-Your-Data app (Windows).
REM Boots the FastAPI backend on :8000 and the Vite dev server on :5173.

setlocal
set ROOT_DIR=%~dp0
set BACKEND_DIR=%ROOT_DIR%backend
set FRONTEND_DIR=%ROOT_DIR%

echo [1/3] Setting up Python backend...
cd /d "%BACKEND_DIR%"

if not exist ".venv" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if not exist ".env" (
    echo [!] backend\.env not found. Copying .env.example to .env.
    copy /Y .env.example .env >nul
    echo     Edit backend\.env and set OPENROUTER_API_KEY before asking AI questions.
)

echo [2/3] Starting backend on http://localhost:8000 ...
start "ChatWithData-Backend" cmd /k "cd /d %BACKEND_DIR% && call .venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo [3/3] Starting frontend on http://localhost:5173 ...
cd /d "%FRONTEND_DIR%"
if not exist "node_modules" (
    call npm install
)
call npm run dev

endlocal
