"""Command line entry point."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from . import checks, report, scan
from .session import PytestFailed, default_pytest_command, probe

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uncollected",
        description="Find the tests pytest never runs.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="the project to check (default: the current directory)",
    )
    parser.add_argument(
        "--pytest",
        metavar="CMD",
        default=None,
        help=(
            "the pytest to ask, as a shell word list "
            f"(default: {' '.join(default_pytest_command())}). "
            "Point this at the project's own virtualenv if it is not this one"
        ),
    )
    parser.add_argument(
        "--only",
        metavar="CHECK",
        action="append",
        default=[],
        help="run only this check; repeatable. One of: " + ", ".join(checks.ALL_CHECKS),
    )
    parser.add_argument(
        "--skip",
        metavar="CHECK",
        action="append",
        default=[],
        help="do not run this check; repeatable",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--list-checks",
        action="store_true",
        help="print the check names and exit",
    )
    return parser


class BadUsage(Exception):
    """Arguments that parse but name something that does not exist."""


def _enabled(only: list[str], skip: list[str]) -> set[str]:
    unknown = [c for c in (*only, *skip) if c not in checks.ALL_CHECKS]
    if unknown:
        raise BadUsage(
            f"uncollected: no such check: {', '.join(unknown)}\n"
            f"             known checks: {', '.join(checks.ALL_CHECKS)}"
        )
    enabled = set(only) if only else set(checks.ALL_CHECKS)
    return enabled - set(skip)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_checks:
        for check in checks.ALL_CHECKS:
            print(check)
        return EXIT_CLEAN

    try:
        enabled = _enabled(args.only, args.skip)
    except BadUsage as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR

    path = Path(args.path)
    if not path.is_dir():
        print(f"uncollected: not a directory: {args.path}", file=sys.stderr)
        return EXIT_ERROR

    command = shlex.split(args.pytest) if args.pytest else None
    try:
        session = probe(path, command)
    except PytestFailed as exc:
        print(f"uncollected: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # Scanning is anchored on pytest's rootdir, not on the path given, so that
    # the relative paths from `--collect-only` and the ones from the scan are
    # the same strings and can be compared at all.
    root = session.rootdir
    if not root.is_dir():
        print(f"uncollected: pytest reported a rootdir that is not there: {root}", file=sys.stderr)
        return EXIT_ERROR

    modules, unparseable = scan.walk(
        root, session.opt("python_files"), session.opt("python_functions")
    )
    findings = checks.run(modules, session, root, enabled)
    candidates = sum(len(module.candidates) for module in modules)

    if args.json:
        report.as_json(findings, session, candidates, unparseable, sys.stdout)
    else:
        report.text(findings, session, candidates, unparseable, sys.stdout)

    return EXIT_FINDINGS if findings else EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
