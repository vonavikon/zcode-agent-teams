"""Engine versioning (hardening spec 8).

The versions evolve independently and are documented in SPEC.md:
the plugin and engine versions describe behavior, SPEC_VERSION the task
specification format, storage.SCHEMA_VERSION the SQLite schema (with
migrations), verification.REPORT_SCHEMA_VERSION the report format.
"""
ENGINE_VERSION = "2.1.0a1"
SPEC_VERSION = "1.0"
