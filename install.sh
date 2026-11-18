#!/usr/bin/env bash
# ESPECTRA — instalador (Linux/macOS)
#   ./install.sh            instala todo
#   ./install.sh --libs     además descarga las bibliotecas de materiales
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
BOLD=$'\033[1m'; GREEN=$'\033[32m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
say() { printf "%s%s%s\n" "$CYAN" "$1" "$RESET"; }
ok()  { printf "%s✓ %s%s\n" "$GREEN" "$1" "$RESET"; }

say "═══ ESPECTRA · instalador ═══"

# --- 1. entorno Python
if [ ! -d .venv ]; then
  say "Creando entorno virtual…"
  "$PY" -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "Python: dependencias instaladas (.venv)"

# --- 2. interfaz React
if command -v npm >/dev/null 2>&1; then
  say "Compilando interfaz (frontend)…"
  (cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent)
  ok "Interfaz compilada en frontend/dist"
else
  echo "AVISO: npm no encontrado — instala Node.js (>=18) y ejecuta:"
  echo "       cd frontend && npm install && npm run build"
fi

# --- 3. bibliotecas de materiales (opcional)
if [ "${1:-}" = "--libs" ]; then
  say "Descargando bibliotecas de materiales (USGS, pigmentos, pinturas)…"
  .venv/bin/python scripts/fetch_usgs.py || true
  .venv/bin/python scripts/fetch_pigments.py || true
  .venv/bin/python scripts/fetch_revigo.py || true
  ok "Bibliotecas actualizadas"
fi

# --- 4. comprobación
say "Comprobando…"
.venv/bin/python -c "import espectro, fastapi, cv2, PIL; print('   módulos OK')"
[ -f frontend/dist/index.html ] && ok "frontend/dist presente" || true

cat <<EOF

${BOLD}Instalado. Para arrancar:${RESET}
  .venv/bin/python server.py        # → http://localhost:8000

  Prueba rápida:   .venv/bin/python -m espectro demo
  Tests:           .venv/bin/python tests/run_tests.py
EOF
