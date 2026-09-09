# Trinity Stack — player shortcuts (no secrets; keys stay in .env / env).

PYTHON ?= python3

.PHONY: demo preflight help

help:
	@echo "make demo       — player path: preflight + short baseline + XL-2 + restore"
	@echo "make preflight  — offline keys/corpus/Agent Control checklist (no API spend)"
	@echo "Keys: cp .env.example .env  (never commit secrets)"
	@echo "Stuck: https://pandeyaby.github.io/Galileo/troubleshooter/"

demo:
	$(PYTHON) examples/player_demo.py

preflight:
	$(PYTHON) app.py --preflight
