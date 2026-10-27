UV ?= uv
PORT ?= 8000
export PORT
export UV_CACHE_DIR ?= /tmp/recall-uv-cache
.PHONY: setup demo test check eval-fixtures eval-live smoke-live refresh-data browser
setup:
	$(UV) sync --frozen --reinstall-package recall-impact-investigator
	$(UV) run playwright install chromium
demo:
	$(UV) run python -m recall.cli demo
	$(UV) run uvicorn recall.web:app --host 127.0.0.1 --port $(PORT)
test:
	$(UV) run pytest -q
check:
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run mypy src
	$(UV) run python scripts/check_text.py
eval-fixtures:
	$(UV) run python -m recall.cli eval-fixtures
eval-live:
	$(UV) run python -m recall.cli eval-live
smoke-live:
	$(UV) run python -m recall.cli smoke-live
refresh-data:
	$(UV) run python -m recall.cli refresh-data
browser:
	$(UV) run pytest -q -m browser
