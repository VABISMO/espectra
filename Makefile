# ESPECTRA — comandos habituales
PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install install-libs run dev build test demo lint fmt clean

install:            ## crear venv + dependencias + compilar interfaz
	./install.sh

install-libs:       ## instalar y descargar bibliotecas de materiales
	./install.sh --libs

run:                ## arrancar el servidor → http://localhost:8000
	$(PY) server.py

dev:                ## servidor con recarga automática
	$(PY) -m uvicorn server:app --reload --port 8000

build:              ## compilar el frontend React
	cd frontend && npm run build

test:               ## tests end-to-end (motor + API + cámara)
	$(PY) tests/run_tests.py

demo:               ## análisis sintético completo → PASS
	$(PY) -m espectro demo

lint:               ## revisión estática
	$(PIP) install -q ruff
	.venv/bin/ruff check --select F,E9 espectro scripts server.py tests

fmt:                ## formato automático
	.venv/bin/ruff check --fix --select I,RUF100,F401 espectro scripts server.py tests || true

clean:              ## artefactos generados
	rm -rf frontend/dist data/examples resultados .ruff_cache
	find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
