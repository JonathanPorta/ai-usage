PYTHON ?= python3
BLESSED := tools/blessed/scripts
AI_USAGE_CONFIG ?= $(HOME)/.ai-usage/config.json
SANDBOX ?= $(CURDIR)/.sandbox
SNAPSHOT_DIR ?= $(CURDIR)/.sandbox/snapshots

.PHONY: help test check spec-check design-check design-build design-build-check \
	report report-sandbox sandbox fixture fixture-check app-build app-test app-run app-run-sandbox app-snapshot app-snapshot-live launchd-integration-check

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
		'make fixture-check       Fail when the committed app fixture is out of date' \
		'make app-test            Swift unit tests (store, decoding, presentation)' \
		'make app-build           Build macos/build/AI Usage.app (ad-hoc signed)' \
		'make app-run             Build and launch against the installed collector' \
		'make app-run-sandbox     Build and launch against the isolated synthetic sandbox' \
		'make app-snapshot        Render fixture screens (light/dark, 420 pt, short) to SNAPSHOT_DIR' \
		'make app-snapshot-live   Render the live report read-only to SNAPSHOT_DIR (personal data; not for git)' \
		'make launchd-integration-check  Real-launchd lifecycle check with a disposable agent (macOS; explicit only)'

test: ## Run the full unit and black-box suite
	$(PYTHON) -m unittest -v

check: ## Compile supported Python sources and run tests
	$(PYTHON) -m py_compile ai_usage_service.py ai_usage_report.py ai_usage_fixtures.py \
		test_ai_usage_service.py test_ai_usage_report.py macos/scripts/generate_design_tokens.py \
		scripts/launchd_integration_check.py
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

APP := macos/build/AI Usage.app

app-test: ## Swift unit tests for the macOS app core, plus an isolated sandbox end-to-end run
	AI_USAGE_REPO_ROOT="$(CURDIR)" AI_USAGE_TEST_PYTHON="$$(command -v $(PYTHON))" swift test --package-path macos

app-build: ## Assemble and ad-hoc sign macos/build/AI Usage.app
	bash macos/scripts/build_app.sh

app-run: app-build ## Launch the app against the installed collector (reads ~/.ai-usage)
	-@pkill -x AIUsage 2>/dev/null; true
	open "$(APP)"

app-run-sandbox: app-build sandbox ## Launch the app against SANDBOX only (never touches ~/.ai-usage)
	-@pkill -x AIUsage 2>/dev/null; true
	AI_USAGE_CONFIG="$(SANDBOX)/config.json" AI_USAGE_SERVICE=skip \
	AI_USAGE_COLLECTOR="$(CURDIR)/ai_usage_service.py" AI_USAGE_PYTHON="$$(command -v $(PYTHON))" \
	AI_USAGE_STATE_DIR="$(SANDBOX)/app-state" \
	"$(APP)/Contents/MacOS/AIUsage" >"$(SANDBOX)/app.log" 2>&1 &
	@echo "AI Usage running against $(SANDBOX) (log: $(SANDBOX)/app.log)"

app-snapshot: app-build ## Render the real views offscreen from the fixture report (no screen permission needed)
	TZ=America/Denver "$(APP)/Contents/MacOS/AIUsage" --snapshot "$(SNAPSHOT_DIR)/fixture"

app-snapshot-live: app-build ## Render the live report (read-only) offscreen; output contains personal usage data
	"$(APP)/Contents/MacOS/AIUsage" --snapshot "$(SNAPSHOT_DIR)/live" --live

launchd-integration-check: ## Real launchd: install/start/stop/reinstall/rollback on a disposable codes.porta.ai-usage.test.* agent
	$(PYTHON) scripts/launchd_integration_check.py
