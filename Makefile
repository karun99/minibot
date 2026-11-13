# Developer tasks for minibot.
#
#   make install   create .venv and install the project with dev extras
#   make test      run the pytest suite
#   make check     byte-compile the package and run the lockfile check
#   make image     build the container image
#   make clean     remove caches and build output

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install test check lock image clean

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

check:
	$(PYTHON) -m compileall -q minibot tests
	uv lock --check
	sh -n scripts/install.sh

lock:
	uv lock

image:
	docker build -t minibot:dev .

clean:
	rm -rf $(VENV) build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
