.PHONY: help bootstrap lint format typecheck test dev-up dev-down openapi

help:
	@echo "Targets:"
	@echo "  bootstrap   Install dev deps"
	@echo "  lint        Ruff lint"
	@echo "  format      Ruff format (and fix)"
	@echo "  typecheck   Mypy"
	@echo "  test        Pytest"
	@echo "  dev-up      docker compose up (build)"
	@echo "  dev-down    docker compose down -v"
	@echo "  openapi     Export OpenAPI yaml to openapi/"

bootstrap:
	python3 -m pip install -U pip
	python3 -m pip install -e '.[dev,ui]'

lint:
	python3 -m ruff check .

format:
	python3 -m ruff check . --fix
	python3 -m ruff format .

typecheck:
	python3 -m mypy src

test:
	python3 -m pytest

dev-up:
	docker compose up -d --build

dev-down:
	docker compose down -v

openapi:
	python3 scripts/export_openapi.py
