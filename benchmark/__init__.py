"""Standalone benchmark tooling for report-assembly model workloads.

The package deliberately has no dependency on the application database,
workflow, vector store, or web server.  It consumes JSONL captures and calls an
OpenAI-compatible endpoint so a dataset can be replayed on another machine.
"""

BENCHMARK_SCHEMA_VERSION = "1.2"
