.PHONY: help lint test unit-test triage-test shellcheck markdownlint yamllint gitlint work-summary work-summary-test

help:
	@echo "Available targets:"
	@echo "  lint          - Run all linters"
	@echo "  test          - Run linters + unit tests"
	@echo "  unit-test     - Run offline skill tests"
	@echo "  triage-test   - Run offline personal Jira triage tests"
	@echo "  shellcheck    - Check bash scripts with shellcheck"
	@echo "  markdownlint  - Check markdown files with markdownlint"
	@echo "  yamllint      - Check YAML files with yamllint"
	@echo "  gitlint       - Check commit messages with gitlint"
	@echo "  work-summary  - Generate work summary (last 7 days, or DAYS=N)"
	@echo "  work-summary-test - Run offline work summary regression tests"

lint: shellcheck markdownlint yamllint

test: lint unit-test

unit-test:
	@echo "Running cve-agent unit tests..."
	@bash skills/cve-agent/scripts/test.sh --level 1
	@$(MAKE) triage-test work-summary-test

triage-test:
	@python3 skills/personal-jira-triage/tests/test_triage.py

shellcheck:
	@echo "Running shellcheck..."
	@find skills -type f -name "*.sh" -exec shellcheck -S warning {} +

markdownlint:
	@echo "Running markdownlint..."
	@npx markdownlint-cli2 "**/*.md" "#node_modules"

yamllint:
	@echo "Running yamllint..."
	@yamllint --strict .

gitlint:
	@echo "Running gitlint..."
	@gitlint --commits origin/main..HEAD

work-summary-test:
	@python3 skills/work-summary/tests/test_work_summary.py

work-summary:
	@set -eu; run_dir=$$(mktemp -d "$${TMPDIR:-/tmp}/work-summary.XXXXXX"); \
	  bash skills/work-summary/scripts/work-summary.sh "$${DAYS:-7}" > "$$run_dir/data.json"; \
	  bash skills/work-summary/scripts/render-report.sh "$$run_dir/data.json" > "$$run_dir/report.md"; \
	  cat "$$run_dir/report.md"; \
	  echo "Report saved to $$run_dir/report.md" >&2
