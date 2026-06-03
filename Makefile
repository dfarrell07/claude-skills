.PHONY: help lint test unit-test shellcheck markdownlint yamllint gitlint work-summary

help:
	@echo "Available targets:"
	@echo "  lint          - Run all linters"
	@echo "  test          - Run linters + unit tests"
	@echo "  unit-test     - Run cve-agent unit tests (Level 1)"
	@echo "  shellcheck    - Check bash scripts with shellcheck"
	@echo "  markdownlint  - Check markdown files with markdownlint"
	@echo "  yamllint      - Check YAML files with yamllint"
	@echo "  gitlint       - Check commit messages with gitlint"
	@echo "  work-summary  - Generate work summary (last 7 days, or DAYS=N)"

lint: shellcheck markdownlint yamllint

test: lint unit-test

unit-test:
	@echo "Running cve-agent unit tests..."
	@bash skills/cve-agent/scripts/test.sh --level 1

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

work-summary:
	@bash skills/work-summary/scripts/work-summary.sh $${DAYS:-7} > /tmp/work-summary-data.json
	@bash skills/work-summary/scripts/render-report.sh /tmp/work-summary-data.json
