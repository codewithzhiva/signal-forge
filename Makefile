.PHONY: bootstrap lint test integration demo benchmark verify
bootstrap:
	uv sync --dev
	cd web && npm install
lint:
	uv run ruff check .
	uv run mypy app
test:
	uv run pytest -q
integration: test
demo:
	docker compose up --build
benchmark:
	uv run python scripts/benchmark.py
verify: lint test
	cd web && npm run build
