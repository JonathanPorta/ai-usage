PYTHON ?= python3
BLESSED := tools/blessed/scripts

.PHONY: help test check spec-check design-check design-build design-build-check

help: ## List supported commands
	@printf '%s\n' \
		'make help                List supported commands' \
		'make test                Run the full unit and black-box suite' \
		'make check               Compile supported Python sources and run tests' \
		'make spec-check          Blessed spec-pack check (ARGS=--strict to fail)' \
		'make design-check        Blessed design-handoff check (ARGS=--strict to fail)' \
		'make design-build        Generate Swift tokens from macos/design/tokens.json' \
		'make design-build-check  Fail when generated tokens drift from the source'

test: ## Run the full unit and black-box suite
	$(PYTHON) -m unittest -v

check: ## Compile supported Python sources and run tests
	$(PYTHON) -m py_compile ai_usage_service.py test_ai_usage_service.py macos/scripts/generate_design_tokens.py
	$(MAKE) test

spec-check: ## Vendored Blessed spec-pack check; report-only, ARGS=--strict in CI
	@bash $(BLESSED)/spec-check.sh $(ARGS)

design-check: ## Vendored Blessed handoff check; report-only, ARGS=--strict in CI
	@bash $(BLESSED)/design-check.sh $(ARGS)

design-build: ## Generate DesignTokens.generated.swift from the canonical DTCG tokens
	$(PYTHON) macos/scripts/generate_design_tokens.py

design-build-check: ## Fail when the generated Swift tokens drift from tokens.json
	$(PYTHON) macos/scripts/generate_design_tokens.py --check
