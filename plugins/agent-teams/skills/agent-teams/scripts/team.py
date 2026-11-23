#!/usr/bin/env python3
"""agent-teams engine CLI (v1.0): thin launcher over the agent_teams package.

The v0.2 single-file engine is kept as team_v02.py for old teams created
before v0.3 (JSON files without state.db). New teams use the SQLite
engine with the state machine, lead/execution tokens, workspace
isolation, the machine quality gate and the integration gate.

Usage: python team.py <command> --team <id> ... --project-root <root>
"""
import sys

from agent_teams.cli import main

if __name__ == "__main__":
    sys.exit(main())
