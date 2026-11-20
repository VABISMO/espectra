# ESPECTRA — common commands
PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install install-libs run dev build test demo bench lint fmt clean

install:            ## create venv + deps + build the frontend
	./install.sh

install-libs:       ## install and download the material libraries
	./install.sh --libs

run:                ## start the server → http://localhost:8000
	$(PY) server.py

dev:                ## server with automatic reload
	$(PY) -m uvicorn server --reload --port 8000

build:              ## build the React frontend
	cd frontend && npm run build

test:               ## end-to-end tests (engine + API + camera)
	$(PY) tests/run_tests.py

demo:               ## full synthetic analysis → PASS
	$(PY) -m espectro demo

bench:              ## LOO validation: top-1/top-3 + false positives
	$(PY) scripts/benchmark.py --n 200 --negatives 40 --sweep

lint:               ## static checks
	$(PIP) install -q ruff
	.venv/bin/ruff check --select F,E9 espectro scripts server.py tests

fmt:                ## automatic formatting
	.venv/bin/ruff check --fix --select I,RUF100,F401 espectro scripts server.py tests || true

clean:              ## generated artefacts
	rm -rf frontend/dist data/examples results .ruff_cache
	find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
