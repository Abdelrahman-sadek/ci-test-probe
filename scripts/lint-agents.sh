#!/usr/bin/env bash
# Validates agents, skills, the plugin marketplace and smoke-test coverage (see scripts/lint_agents.py).
exec python3 "$(dirname "$0")/lint_agents.py" "$@"
