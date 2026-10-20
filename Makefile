PYTHON ?= python3
BLESSED := tools/blessed/scripts
AI_USAGE_CONFIG ?= $(HOME)/.ai-usage/config.json
SANDBOX ?= $(CURDIR)/.sandbox
SNAPSHOT_DIR ?= $(CURDIR)/.sandbox/snapshots

# Release candidates (releases.pipeline@1 / releases.candidate@1 pilots).
SCRIPT_CATEGORIES ?= release signing
CANDIDATE_DIR ?= dist/candidate
APP_VERSION = $(shell scripts/release-tools/check-versions.sh)
SOURCE_SHA ?= $(shell git rev-parse HEAD)
BUILD_SHA ?= $(SOURCE_SHA)
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct $(SOURCE_SHA) 2>/dev/null)
CREATED_AT ?= $(shell date -u -r $(SOURCE_DATE_EPOCH) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d @$(SOURCE_DATE_EPOCH) +%Y-%m-%dT%H:%M:%SZ)
RELEASE_KEY_ID ?= ai-usage-release-2026-10
SIGNING_KEY_ENV ?= AI_USAGE_RELEASE_SIGNING_KEY
TRUST_STORE ?= release-trusted-keys.json
CANDIDATE_ARCHIVE = ai-usage_$(APP_VERSION)_arm64.app.tar.gz

.PHONY: help test check spec-check design-check design-build design-build-check \
	report report-sandbox sandbox fixture fixture-check app-build app-test app-run app-run-sandbox app-snapshot app-snapshot-check app-snapshot-live launchd-integration-check \
	sync-scripts verify-scripts version-check release-plan-check release-build release-assemble \
	release-sign release-validate release release-check publish

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
		'make app-snapshot-check  Render fixture screens and compare them with the accepted baselines' \
		'make app-snapshot-live   Render the live report read-only to SNAPSHOT_DIR (personal data; not for git)' \
		'make launchd-integration-check  Real-launchd lifecycle check with a disposable agent (macOS; explicit only)' \
		'make verify-scripts      Verify vendored blessed-cicd release/signing scripts against their manifests' \
		'make release-plan-check  Validate the candidate plan (secret-free; any OS)' \
		'make release-check       Build, assemble, sign with an EPHEMERAL key and validate a candidate (macOS; secret-free)' \
		'make release             Seal a candidate with the production key (needs the release-signing domain)' \
		'make publish             Refuses: publication is disabled (docs/CI-AND-RELEASE.md)'

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

app-snapshot-check: app-snapshot ## Compare fresh fixture renders byte-for-byte with docs/design/app/references
	cd "$(SNAPSHOT_DIR)/fixture" && shasum -a 256 -c "$(CURDIR)/docs/design/app/references/SHA256SUMS"

app-snapshot-live: app-build ## Render the live report (read-only) offscreen; output contains personal usage data
	"$(APP)/Contents/MacOS/AIUsage" --snapshot "$(SNAPSHOT_DIR)/live" --live

launchd-integration-check: ## Real launchd: install/start/stop/reinstall/rollback on a disposable codes.porta.ai-usage.test.* agent
	$(PYTHON) scripts/launchd_integration_check.py

# ── Blessed scripts (signed public export, pinned in .blessed/export.env) ──
sync-scripts: ## Install the pinned, signed public blessed-cicd export (.blessed/export.env); review, then commit
	scripts/install-blessed-export.sh

verify-scripts: ## Verify installed blessed-cicd scripts against MANIFEST.sha256
	@rc=0; for c in $(SCRIPT_CATEGORIES); do \
	  if ( cd scripts/$$c && shasum -a 256 -c MANIFEST.sha256 >/dev/null 2>&1 ); then \
	    echo "scripts/$$c v$$(cat scripts/$$c/VERSION 2>/dev/null): OK"; \
	  else \
	    echo "scripts/$$c: DRIFT — files differ from MANIFEST.sha256"; rc=1; \
	  fi; \
	done; exit $$rc

# ── Release boundaries (releases.pipeline@1) ─────────────────────────────────
# release-build is secret-free; release-assemble seals the closed candidate under
# dist/; release-sign is the only step that touches a key (by env-var name only);
# publish never runs (publication is disabled for this public repository).
version-check: ## Print the app version; fail unless it is MAJOR.MINOR.PATCH (EXPECT_TAG optional)
	@scripts/release-tools/check-versions.sh $(if $(EXPECT_TAG),--expect-tag $(EXPECT_TAG))

release-plan-check: verify-scripts ## Generate and validate the candidate plan (no build, no key)
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	scripts/release-tools/make-plan.sh --version $(APP_VERSION) --source-sha $(SOURCE_SHA) \
	  --build-sha $(BUILD_SHA) --created-at $(CREATED_AT) --key-id $(RELEASE_KEY_ID) \
	  --output "$$tmp/candidate-plan.json" && \
	scripts/release/validate-plan.sh --plan "$$tmp/candidate-plan.json"

release-build: version-check verify-scripts ## Build the declared artifact (no signing key, no network)
	@[ -z "$$(git status --porcelain --untracked-files=no)" ] || { echo "release-build: the working tree has uncommitted changes; a candidate must match SOURCE_SHA"; exit 1; }
	$(MAKE) app-build
	@mkdir -p $(CANDIDATE_DIR)
	SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) scripts/release-tools/archive-app.sh \
	  --app "$(APP)" --output "$(CANDIDATE_DIR)/$(CANDIDATE_ARCHIVE)"

release-assemble: verify-scripts ## Assemble the closed candidate (notes, plan, provenance, SHA256SUMS, manifest)
	@mkdir -p $(CANDIDATE_DIR)
	cp release-notes-public.md $(CANDIDATE_DIR)/
	scripts/release-tools/make-plan.sh --version $(APP_VERSION) \
	  --source-sha $(SOURCE_SHA) --build-sha $(BUILD_SHA) \
	  --created-at $(CREATED_AT) --key-id $(RELEASE_KEY_ID) \
	  --output $(CANDIDATE_DIR)/candidate-plan.json
	scripts/release/validate-plan.sh --plan $(CANDIDATE_DIR)/candidate-plan.json
	scripts/release/build-candidate.sh --plan $(CANDIDATE_DIR)/candidate-plan.json \
	  --candidate-dir $(CANDIDATE_DIR) \
	  --output $(CANDIDATE_DIR)/release-candidate.json
	@rm -f $(CANDIDATE_DIR)/candidate-plan.json

release-sign: verify-scripts ## Sign the candidate manifest with the key named by SIGNING_KEY_ENV
	@[ -n "$$$(SIGNING_KEY_ENV)" ] || { \
	  echo "$(SIGNING_KEY_ENV) is not set: the candidate manifest cannot be signed."; \
	  echo "Production signing uses the release-signing authority domain (docs/CI-AND-RELEASE.md)."; \
	  exit 1; \
	}
	scripts/signing/sign.sh --profile ed25519-detached-v1 \
	  --input $(CANDIDATE_DIR)/release-candidate.json \
	  --private-key-env $(SIGNING_KEY_ENV) \
	  --output $(CANDIDATE_DIR)/release-candidate.json.sig --force

release-validate: verify-scripts ## Validate schema, closure, on-disk hashes and the manifest signature
	scripts/release/validate-candidate.sh --candidate-dir $(CANDIDATE_DIR) \
	  --trust-store $(TRUST_STORE) --signing-dir scripts/signing

release: release-build release-assemble release-sign release-validate ## Seal a complete candidate under dist/ (no remote writes)
	@echo "Candidate sealed in $(CANDIDATE_DIR) for $(APP_VERSION); nothing has been published."

release-check: ## Secret-free end to end: build, assemble, sign with an EPHEMERAL key, validate (macOS)
	@set -e; tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	kid=ai-usage-ephemeral; \
	scripts/signing/keygen.sh --out-private "$$tmp/key.pem" --out-public-base64 "$$tmp/key.pub" --key-id "$$kid" >/dev/null; \
	jq -n --arg kid "$$kid" --arg pub "$$(cat "$$tmp/key.pub")" \
	  '{schema: "blessed/signing-trust-store/v1", notes: "ephemeral, secret-free validation", keys: {($$kid): {profile: "ed25519-detached-v1", public_key_base64: $$pub, status: "active"}}}' > "$$tmp/trust.json"; \
	rm -rf $(CANDIDATE_DIR); \
	AI_USAGE_EPHEMERAL_KEY="$$(cat "$$tmp/key.pem")" $(MAKE) --no-print-directory release-build release-assemble release-sign release-validate \
	  RELEASE_KEY_ID="$$kid" SIGNING_KEY_ENV=AI_USAGE_EPHEMERAL_KEY TRUST_STORE="$$tmp/trust.json"; \
	echo "release-check: candidate built, sealed, signed with an ephemeral key and validated; nothing published"

publish: ## Refuses: publication is disabled until the owner approves it
	@echo "publish: disabled. ai-usage is public, so any GitHub Release (even a prerelease) is public."
	@echo "Publication needs the owner's approval, a production manifest key in release-trusted-keys.json,"
	@echo "and Developer ID signing; see docs/CI-AND-RELEASE.md."
	@exit 1
