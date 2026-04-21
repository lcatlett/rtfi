.PHONY: test lint typecheck format install dev clean

install:
	@echo "No dependencies to install — RTFI uses Python stdlib only."

dev:
	$(if $(shell command -v uv 2>/dev/null),uv pip install,pip install) pytest pytest-cov mypy ruff

test:
	pytest tests/ -v --tb=short

lint:
	ruff check scripts/ tests/

typecheck:
	mypy scripts/rtfi_core.py scripts/hook_handler.py scripts/rtfi_dashboard.py scripts/rtfi_cli.py

format:
	ruff format scripts/ tests/

clean:
	rm -rf __pycache__ .mypy_cache .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
