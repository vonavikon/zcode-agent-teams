#!/usr/bin/env python3
"""Launcher for the agent-teams v0.3 engine CLI.

Usage: python ateam.py <command> --team <id> ... --project-root <root>
Run with --help for the command list. The v0.2 engine (team.py) stays in
place until the v1.0 release switches it over.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_teams.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
