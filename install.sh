#!/usr/bin/env bash
# ESPECTRA — installer (Linux/macOS)
#   ./install.sh            installs everything
#   ./install.sh --libs     also downloads the material libraries
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
BOLD=$'\033[1m'; GREEN=$'\033[32m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
say() { printf "%s%s%s\n" "$CYAN" "$1" "$RESET"; }
ok()  { printf "%s✓ %s%s\n" "$GREEN" "$1" "$RESET"; }

say "═══ ESPECTRA · installer ═══"

# --- 1. Python environment
if [ ! -d .venv ]; then
  say "Creating virtual environment…"
  "$PY" -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "Python: dependencies installed (.venv)"

# --- 2. React frontend
if command -v npm >/dev/null 2>&1; then
  say "Building the frontend…"
  (cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent)
  ok "Frontend built into frontend/dist"
else
  echo "WARNING: npm not found — install Node.js (>=18) and run:"
  echo "         cd frontend && npm install && npm run build"
fi

# --- 3. material libraries (optional)
if [ "${1:-}" = "--libs" ]; then
  say "Downloading material libraries (USGS, pigments, paints)…"
  .venv/bin/python scripts/fetch_usgs.py || true
  .venv/bin/python scripts/fetch_pigments.py || true
  .venv/bin/python scripts/fetch_revigo.py || true
  ok "Libraries updated"
fi

# --- 4. sanity check
say "Checking…"
.venv/bin/python -c "import espectro, fastapi, cv2, PIL; print('   modules OK')"
[ -f frontend/dist/index.html ] && ok "frontend/dist present" || true

cat <<EOF

${BOLD}Installed. To start:${RESET}
  .venv/bin/python server.py        # → http://localhost:8000

  Quick test:    .venv/bin/python -m espectro demo
  Tests:         .venv/bin/python tests/run_tests.py
EOF
