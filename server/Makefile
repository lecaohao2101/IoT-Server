# Convenience targets. On Windows run the commands directly or use `make` from Git Bash.
VENV ?= .venv
PY   ?= $(VENV)/Scripts/python.exe

.PHONY: help venv install install-ai dev run test lint fmt sim apartment clean

help:
	@echo "venv        tao virtualenv"
	@echo "install     cai dependency cot loi + dev"
	@echo "install-ai  cai them cac provider Google (STT/TTS/Gemini)"
	@echo "run         chay server (doc .env)"
	@echo "dev         chay server voi auto-reload"
	@echo "test        chay toan bo test"
	@echo "lint        kiem tra ruff"
	@echo "sim         gia lap ESP32 gui mot cau noi"
	@echo "apartment   gia lap toan bo thiet bi tren MQTT"

venv:
	python -m venv $(VENV)

install: venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

install-ai:
	$(PY) -m pip install -r requirements-ai.txt

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

dev:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check app tests tools

fmt:
	$(PY) -m ruff check --fix app tests tools

sim:
	$(PY) tools/simulate_client.py --say "bật đèn phòng khách lên 70 phần trăm"

apartment:
	$(PY) tools/fake_device.py
