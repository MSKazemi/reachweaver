# Offline developer checks. None of these targets contacts a social platform or needs credentials.
PYTHON ?= python3
RUFF_VERSION ?= 0.16.10
MYPY_VERSION ?= 2.4.0
BUILD_VERSION ?= 1.6.1
TWINE_VERSION ?= 7.0.0

.PHONY: help test sync scan lint deps ruff typecheck format-check example check release-gate package plugin-eval plugin-eval-load site site-check site-dev

help:
	@echo "make test     - offline unit tests (no network, no credentials)"
	@echo "make sync     - mirror reachweaver/ into the bundled Claude Code skill"
	@echo "make scan     - secret scan of tracked files (prints path:line:pattern only)"
	@echo "make lint     - stdlib lint: syntax of tracked .py, JSON/JSONL validity"
	@echo "make deps     - assert pyproject declares no runtime dependencies (else pip-audit is required)"
	@echo "make ruff     - optional: ruff $(RUFF_VERSION) hard-error rules via pipx (downloads ruff)"
	@echo "make typecheck - optional: mypy $(MYPY_VERSION) on reachweaver/ via pipx (downloads mypy)"
	@echo "make format-check - advisory, never fails: ruff format --check (adoption deferred, ADR-0002)"
	@echo "make package  - build the wheel and sdist, twine check, check contents, install and run (downloads build and twine via pipx)"
	@echo "make example  - offline example run against a throwaway database"
	@echo "make check    - sync + scan + lint + deps + test (what CI runs, minus ruff and mypy)"
	@echo "make release-gate [TAG=vX.Y.Z] - scan + lint + deps, then the offline release gate (versions, CHANGELOG, docs, bundle, blockers, tests)"
	@echo "make plugin-eval [PLUGIN_EVAL_ARGS='--case NAME --runs 1 --ablation none'] - LOCAL ONLY: plugin behaviour suite, real model calls on your Claude plan (docs/PLUGIN-EVALS.md)"
	@echo "make site     - documentation website: generate content from the Markdown, npm ci, build website/dist (needs Node.js 22.23+)"
	@echo "make site-check - check the built site: titles, descriptions, canonicals, sitemap, JSON-LD, no private links"
	@echo "make site-dev - live preview of the documentation website at http://localhost:4321/reachweaver/"
	@echo "make plugin-eval-load - LOCAL ONLY: load and schema-check the suite with a 0 USD ceiling; starts no run (needs the claude CLI)"

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

# Build the wheel and sdist into a throwaway folder and run the same checks CI and the release workflow run
# (downloads build and twine via pipx; the package itself needs no network). Build from a CLEAN checkout when
# you intend to publish: a working folder can hold files that must never reach a package.
package:
	@tmp=$$(mktemp -d) && \
	pipx run --spec build==$(BUILD_VERSION) pyproject-build --outdir $$tmp/dist . && \
	pipx run --spec twine==$(TWINE_VERSION) twine check --strict $$tmp/dist/* && \
	$(PYTHON) scripts/check_package.py $$tmp/dist --smoke && \
	rm -rf $$tmp build reachweaver.egg-info

# Plugin behaviour suite (docs/PLUGIN-EVALS.md). LOCAL ONLY, never part of `check` or CI: every run and every judge
# grader is a real model call on your own Claude plan. Bash, Write and Edit are granted so the "never runs approve or
# publish --live" graders can fail; Claude Code runs Bash under its OS sandbox. Results go to the ignored state/ folder.
CLAUDE ?= claude
PLUGIN_EVAL_TOOLS ?= Bash Write Edit
PLUGIN_EVAL_OUT ?= state/plugin-evals
PLUGIN_EVAL_ARGS ?=
plugin-eval:
	$(CLAUDE) plugin eval plugin --allow-tools $(PLUGIN_EVAL_TOOLS) --no-publish \
	  --output-dir $(PLUGIN_EVAL_OUT)/$$(date -u +%Y%m%dT%H%M%SZ) $(PLUGIN_EVAL_ARGS)

# Parse every case with the real CLI and a 0 USD ceiling: load errors are printed, no agent run starts (observed on
# Claude Code 2.1.304: exit 2 "partial (cost ceiling hit)", $0.00). --trust-plugin because the output is captured.
plugin-eval-load:
	@tmp=$$(mktemp -d) && \
	out=$$($(CLAUDE) plugin eval plugin --ablation none --runs 1 --max-cost-usd 0 --no-publish --trust-plugin \
	  --allow-tools $(PLUGIN_EVAL_TOOLS) --output-dir $$tmp/out < /dev/null 2>&1); status=$$?; rm -rf $$tmp; \
	echo "$$out"; \
	if echo "$$out" | grep -q "failed to load"; then exit 1; fi; \
	if [ $$status -ne 0 ] && [ $$status -ne 2 ]; then exit $$status; fi; \
	echo "plugin/evals: every case loaded (no run started)"

# Documentation website (website/, Astro Starlight). The content is generated from the repository's Markdown.
site:
	$(PYTHON) scripts/build_docs_site.py
	cd website && npm ci --no-audit --no-fund && npm run build

site-check:
	$(PYTHON) scripts/check_docs_site.py website/dist

site-dev:
	$(PYTHON) scripts/build_docs_site.py
	cd website && npm ci --no-audit --no-fund && npm run dev
