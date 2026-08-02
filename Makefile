PYTHON ?= python3

.PHONY: help test check

help: ## List supported commands
	@printf '%s\n' \
		'make help   List supported commands' \
		'make test   Run the full unit and black-box suite' \
		'make check  Compile supported Python sources and run tests'

test: ## Run the full unit and black-box suite
	$(PYTHON) -m unittest -v

check: ## Compile supported Python sources and run tests
	$(PYTHON) -m py_compile ai_usage_service.py test_ai_usage_service.py
	$(MAKE) test
