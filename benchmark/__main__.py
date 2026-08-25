"""CLI entry point for data building and independent replay."""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report-assembly standalone benchmark tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="build JSON benchmark dataset from capture JSONL")
    subparsers.add_parser("micro", help="generate fully synthetic context/decode cases")
    subparsers.add_parser("run", help="replay a dataset against an OpenAI-compatible endpoint")
    subparsers.add_parser("export-profile", help="export existing application profile to JSON")
    args, rest = parser.parse_known_args(argv)
    if args.command == "build":
        from benchmark.dataset import main as command
    elif args.command == "micro":
        from benchmark.micro import main as command
    elif args.command == "run":
        from benchmark.runner import main as command
    else:
        from benchmark.export_profile import main as command
    return command(rest)


if __name__ == "__main__":
    raise SystemExit(main())
