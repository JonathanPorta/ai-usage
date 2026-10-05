PYTHON ?= python3
BLESSED := tools/blessed/scripts
AI_USAGE_CONFIG ?= $(HOME)/.ai-usage/config.json
SANDBOX ?= $(CURDIR)/.sandbox

.PHONY: help test check spec-check design-check design-build design-build-check \
	report report-sandbox sandbox fixture fixture-check

help: ## List supported commands
	@printf '%s\n' \
		'make help                List supported commands' \
		'make test                Run the full unit and black-box suite' \
		'make check               Compile supported Python sources and run tests' \
		'make spec-check          Blessed spec-pack check (ARGS=--strict to fail)' \
		'make design-check        Blessed design-handoff check (ARGS=--strict to fail)' \
		'make design-build        Generate Swift tokens from macos/design/tokens.json' \
		'make design-build-check  Fail when generated tokens drift from the source' \
		'make report              Print the JSON report for AI_USAGE_CONFIG (read-only)' \
		'make report-sandbox      Print the JSON report for an isolated synthetic sandbox' \
		'make sandbox             (Re)create the synthetic sandbox in SANDBOX' \
		'make fixture             Regenerate the app fixture report' \
		'make fixture-check       Fail when the committed app fixture is out of date'

test: ## Run the full unit and black-box suite
	$(PYTHON) -m unittest -v

check: ## Compile supported Python sources and run tests
	$(PYTHON) -m py_compile ai_usage_service.py ai_usage_report.py ai_usage_fixtures.py \
		test_ai_usage_service.py test_ai_usage_report.py macos/scripts/generate_design_tokens.py
	$(MAKE) test

spec-check: ## Vendored Blessed spec-pack check; report-only, ARGS=--strict in CI
	@bash $(BLESSED)/spec-check.sh $(ARGS)

design-check: ## Vendored Blessed handoff check; report-only, ARGS=--strict in CI
	@bash $(BLESSED)/design-check.sh $(ARGS)

design-build: ## Generate DesignTokens.generated.swift from the canonical DTCG tokens
	$(PYTHON) macos/scripts/generate_design_tokens.py

design-build-check: ## Fail when the generated Swift tokens drift from tokens.json
	$(PYTHON) macos/scripts/generate_design_tokens.py --check

report: ## Print the read-only JSON report for AI_USAGE_CONFIG (default: the installed collector)
	@$(PYTHON) ai_usage_report.py --config "$(AI_USAGE_CONFIG)" $(ARGS)

sandbox: ## Recreate an isolated synthetic collector home in SANDBOX (never touches ~/.ai-usage)
	@rm -rf "$(SANDBOX)"
	@$(PYTHON) ai_usage_fixtures.py sandbox "$(SANDBOX)" >&2

report-sandbox: sandbox ## Print the JSON report for the synthetic sandbox
	@$(PYTHON) ai_usage_report.py --config "$(SANDBOX)/config.json" --service skip $(ARGS)

fixture: ## Regenerate the committed app fixture report from the synthetic scenario
	$(PYTHON) ai_usage_fixtures.py fixture

fixture-check: ## Fail when the committed app fixture drifts from the generator
	$(PYTHON) ai_usage_fixtures.py fixture-check
