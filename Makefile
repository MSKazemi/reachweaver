# Offline developer checks. None of these targets contacts a social platform or needs credentials.
PYTHON ?= python3
RUFF_VERSION ?= 0.16.10
MYPY_VERSION ?= 2.4.0

.PHONY: help test sync scan lint deps ruff typecheck format-check example check release-gate

help:
	@echo "make test     - offline unit tests (no network, no credentials)"
	@echo "make sync     - mirror reachweaver/ into the bundled Claude Code skill"
	@echo "make scan     - secret scan of tracked files (prints path:line:pattern only)"
	@echo "make lint     - stdlib lint: syntax of tracked .py, JSON/JSONL validity"
	@echo "make deps     - assert pyproject declares no runtime dependencies (else pip-audit is required)"
	@echo "make ruff     - optional: ruff $(RUFF_VERSION) hard-error rules via pipx (downloads ruff)"
	@echo "make typecheck - optional: mypy $(MYPY_VERSION) on reachweaver/ via pipx (downloads mypy)"
	@echo "make format-check - advisory, never fails: ruff format --check (adoption deferred, ADR-0002)"
	@echo "make example  - offline example run against a throwaway database"
	@echo "make check    - sync + scan + lint + deps + test (what CI runs, minus ruff and mypy)"
	@echo "make release-gate [TAG=vX.Y.Z] - scan + lint + deps, then the offline release gate (versions, CHANGELOG, docs, bundle, blockers, tests)"

test:
	$(PYTHON) -m unittest discover -s tests -v

sync:
	$(PYTHON) scripts/sync_skill.py

scan:
	$(PYTHON) scripts/secret_scan.py

lint:
	$(PYTHON) scripts/lint.py
	$(PYTHON) -m compileall -q reachweaver scripts tests

deps:
	$(PYTHON) scripts/check_runtime_deps.py

ruff:
	pipx run --spec ruff==$(RUFF_VERSION) ruff check --no-cache --select E9,F63,F7,F82 --target-version py311 reachweaver scripts tests

typecheck:
	pipx run --spec mypy==$(MYPY_VERSION) mypy

# Advisory only: the leading '-' makes make ignore the exit status. Reformatting is deferred
# until parallel branches have merged (docs/ADR-0002-RUNTIME-AND-TOOLING.md, decision 9).
format-check:
	-pipx run --spec ruff==$(RUFF_VERSION) ruff format --no-cache --check --target-version py311 reachweaver scripts tests

example:
	@tmp=$$(mktemp -d) && \
	$(PYTHON) -m reachweaver --db $$tmp/example.sqlite init && \
	$(PYTHON) -m reachweaver --db $$tmp/example.sqlite --accounts config/accounts.example.json doctor && \
	$(PYTHON) -m reachweaver campaign-check --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json && \
	rm -rf $$tmp

check: sync scan lint deps test

# Offline, versioned release gate (issue #27). Does not run sync: it verifies the bundled skill
# is already in sync. With TAG=vX.Y.Z it also requires the tag to match and a dated CHANGELOG.
TAG ?=
release-gate: scan lint deps
	$(PYTHON) scripts/release_gate.py $(if $(TAG),--tag $(TAG),)
