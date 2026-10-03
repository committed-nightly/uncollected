"""Printing findings."""

from __future__ import annotations

import json
from typing import Iterable, TextIO

from .checks import Finding
from .session import Session


def _headline(session: Session, candidates: int) -> str:
    where = session.configfile or "no config file"
    return (
        f"pytest {session.pytest_version}, rootdir {session.rootdir}, {where}: "
        f"{len(session.items)} test(s) collected from {candidates} that look like tests"
    )


def text(
    findings: Iterable[Finding],
    session: Session,
    candidates: int,
    unparseable: Iterable[str],
    out: TextIO,
) -> None:
    findings = list(findings)
    unparseable = list(unparseable)

    print(_headline(session, candidates), file=out)

    for problem in unparseable:
        print(f"uncollected: could not read {problem}", file=out)

    if not findings:
        print("uncollected: pytest collects every test in this project", file=out)
        return

    print("", file=out)
    for finding in findings:
        print(f"{finding.location}: {finding.check}: {finding.message}", file=out)
        for line in finding.detail:
            print(f"    {line}", file=out)

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.check] = counts.get(finding.check, 0) + 1
    summary = ", ".join(f"{n} {check}" for check, n in sorted(counts.items()))
    print(f"\n{len(findings)} test(s) you wrote that pytest never runs: {summary}", file=out)


def as_json(
    findings: Iterable[Finding],
    session: Session,
    candidates: int,
    unparseable: Iterable[str],
    out: TextIO,
) -> None:
    payload = {
        "pytest_version": session.pytest_version,
        "rootdir": str(session.rootdir),
        "configfile": session.configfile,
        "ini": session.ini,
        "collected": len(session.items),
        "deselected": len(session.deselected),
        "candidates": candidates,
        "unreadable": list(unparseable),
        "findings": [
            {
                "check": f.check,
                "location": f.location,
                "message": f.message,
                "detail": list(f.detail),
            }
            for f in findings
        ],
    }
    json.dump(payload, out, indent=2, sort_keys=True)
    print("", file=out)
