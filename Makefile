.PHONY: test lint typecheck format install dev clean

install:
	pip install pydantic>=2.0.0

dev:
	pip install pydantic>=2.0.0 pytest pytest-cov mypy ruff

test:
	pytest tests/ -v --tb=short

lint:
	ruff check scripts/ tests/

typecheck:
	mypy scripts/rtfi/

format:
	ruff format scripts/ tests/

clean:
	rm -rf __pycache__ .mypy_cache .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
