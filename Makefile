# ContractSafe Content Agent — Decision Audit System
#
# These targets integrate the decision ledger into the development workflow.

.PHONY: check-action log-decision search-decisions audit-plan check-staleness

# Check a proposed action against the decision ledger (with AI contradiction detection)
# Usage: make check-action ACTION="Add timeout retry logic" TAGS="timeout,pipeline"
check-action:
	@if [ -z "$(ACTION)" ] || [ -z "$(TAGS)" ]; then \
		echo "Usage: make check-action ACTION=\"description\" TAGS=\"tag1,tag2\""; \
		exit 1; \
	fi
	@scripts/gate-action.sh --action "$(ACTION)" --tags "$(TAGS)"

# Log a new decision to the ledger
# Usage: make log-decision DECISION="text" CONTEXT="why" TAGS="tag1,tag2"
log-decision:
	@if [ -z "$(DECISION)" ] || [ -z "$(CONTEXT)" ]; then \
		echo "Usage: make log-decision DECISION=\"text\" CONTEXT=\"why\" TAGS=\"tag1,tag2\""; \
		exit 1; \
	fi
	@scripts/log-decision.sh "$(DECISION)" "$(CONTEXT)" --tags "$(TAGS)"

# Search the decision ledger
# Usage: make search-decisions KEYWORD="enforcement"
search-decisions:
	@if [ -z "$(KEYWORD)" ]; then \
		echo "Usage: make search-decisions KEYWORD=\"enforcement\""; \
		exit 1; \
	fi
	@scripts/search-decisions.sh "$(KEYWORD)"

# Audit a plan file before execution
# Usage: make audit-plan PLAN="plan-file.md"
audit-plan:
	@if [ -z "$(PLAN)" ]; then \
		echo "Usage: make audit-plan PLAN=\"plan-file.md\""; \
		exit 1; \
	fi
	@scripts/audit-plan.sh "$(PLAN)"

# Check if an instruction is stale (superseded by newer decisions)
# Usage: make check-staleness INSTRUCTION="Set timeout to 120s"
check-staleness:
	@if [ -z "$(INSTRUCTION)" ]; then \
		echo "Usage: make check-staleness INSTRUCTION=\"instruction text\""; \
		exit 1; \
	fi
	@scripts/check-staleness.sh "$(INSTRUCTION)"
