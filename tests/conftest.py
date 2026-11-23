"""Shared helpers for the concurrency suites (process-based tests)."""
from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest


@pytest.fixture
def spawn_ctx():
    return mp.get_context("spawn")
