#!/usr/bin/env bash
# One-click launcher for the Chat-with-Your-Data app (macOS / Linux).
# Boots the FastAPI backend on :8000 and the Vite dev server on :5173.

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}"

# --- Backend bootstrap ---
echo "[1/3] Setting up Python backend…"
cd "${BACKEND_DIR}"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f ".env" ]; then
  echo "[!] backend/.env not found. Copying .env.example → .env."
  cp .env.example .env
  echo "    Edit backend/.env and set OPENROUTER_API_KEY before asking AI questions."
fi

# --- Launch backend in the background ---
echo "[2/3] Starting backend on http://localhost:8000 …"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
trap 'kill ${BACKEND_PID} 2>/dev/null || true' EXIT

# --- Frontend bootstrap ---
echo "[3/3] Starting frontend on http://localhost:5173 …"
cd "${FRONTEND_DIR}"

# Locate npm — try common install locations if it isn't already on PATH.
if ! command -v npm >/dev/null 2>&1; then
  for candidate in \
      "$HOME/miniconda3/bin" \
      "$HOME/anaconda3/bin" \
      "/opt/homebrew/bin" \
      "/usr/local/bin" \
      "$HOME/.nvm/versions/node"/*/bin \
      "$HOME/.volta/bin" \
      "$HOME/.fnm/aliases/default/bin"; do
    if [ -x "${candidate}/npm" ]; then
      export PATH="${candidate}:${PATH}"
      echo "    Found npm at ${candidate}/npm"
      break
    fi
  done
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[!] npm not found." >&2
  echo "    Install Node.js (≥ 22.12) first:" >&2
  echo "      • Homebrew:   brew install node" >&2
  echo "      • Official:   https://nodejs.org/" >&2
  echo "      • Conda:      conda install -n base -c conda-forge \"nodejs>=22.12\"" >&2
  echo "    Then re-run ./start.sh." >&2
  exit 1
fi

if [ ! -d "node_modules" ]; then
  npm install
fi
npm run dev
