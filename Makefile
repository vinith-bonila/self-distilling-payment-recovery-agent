# Targets. `make demo` runs the full eval offline (no keys, no network) and
# opens the cost curve. The distillation re-run arrives in Phase 7.

.PHONY: install test demo demo-full

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

demo:
	python -m evals.run

demo-full:
	python -m evals.run --full
