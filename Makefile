# Minimal targets for Phase 1. `make demo` (the one-command offline eval) and
# the full target set arrive in Phase 6 / Phase 9.

.PHONY: install test

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest
