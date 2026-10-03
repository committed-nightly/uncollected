"""Running the project's own pytest and reading back what it collected.

The ground truth for "does this test run?" is pytest, not a model of pytest. So
this runs ``pytest --collect-only`` in the project, with no path arguments, so
that ``testpaths``, ``norecursedirs`` and the project's own ``addopts`` all
apply exactly as they do in its CI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: The module name the probe is loaded under. Deliberately ugly: it goes on
#: PYTHONPATH in the target project's interpreter and must not collide with
#: anything the project has.
PROBE_MODULE = "_uncollected_probe"

#: pytest exit codes we can work with. 0 is "collected something", 5 is
#: "collected nothing" -- which is a perfectly good answer, and often the most
#: interesting one, since every candidate is then uncollected.
OK_EXIT_CODES = (0, 5)

#: pytest's built-in `norecursedirs`. A test file under `build/` or `.tox/` is
#: almost always a stale copy rather than a finding, so these are skipped while
#: walking rather than reported. Patterns the *project* adds are reported.
DEFAULT_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}")

#: Defaults pytest uses when the project says nothing, so the checks still work
#: on a project with no configuration file at all.
INI_DEFAULTS = {
    "python_files": ["test_*.py", "*_test.py"],
    "python_classes": ["Test"],
    "python_functions": ["test"],
    "norecursedirs": list(DEFAULT_NORECURSEDIRS),
    "testpaths": [],
}


class PytestFailed(Exception):
    """pytest could not complete collection, so it has no opinion to report.

    Raised rather than guessed around. A module that fails to import collects
    nothing, and reporting its tests as "never collected" would be true but
    useless -- the import error is the finding, and pytest already printed it.
    """


@dataclass(frozen=True)
class Collected:
    """One test pytest actually produced."""

    nodeid: str
    file: str
    line: int | None
    domain: str

    @property
    def qualname(self) -> str:
        """``TestThing.test_method``, with any parametrisation id removed."""
        return self.domain.split("[", 1)[0]


@dataclass
class Session:
    """What one ``pytest --collect-only`` run had to say."""

    pytest_version: str
    rootdir: Path
    configfile: str | None
    ini: dict[str, list[str]]
    items: list[Collected]
    deselected: list[Collected] = field(default_factory=list)

    def opt(self, name: str) -> list[str]:
        """An ini list setting, falling back to pytest's documented default."""
        value = self.ini.get(name)
        if value is None:
            return list(INI_DEFAULTS.get(name, []))
        return list(value)

    @property
    def collected_keys(self) -> set[tuple[str, str]]:
        """(file, qualname) for everything that ran or was deselected.

        Deselected tests are in here on purpose: a test skipped by
        ``-m "not slow"`` is configured, not lost.
        """
        return {(item.file, item.qualname) for item in (*self.items, *self.deselected)}

    @property
    def collected_files(self) -> set[str]:
        return {item.file for item in (*self.items, *self.deselected)}

    @property
    def major(self) -> int:
        """pytest's major version, or 0 if it is not a number we understand."""
        head = self.pytest_version.split(".", 1)[0]
        return int(head) if head.isdigit() else 0


def default_pytest_command() -> list[str]:
    """``python -m pytest`` for the interpreter running this tool."""
    return [sys.executable, "-m", "pytest"]


def probe(path: Path, pytest_command: list[str] | None = None) -> Session:
    """Collect in `path` and return what pytest reported.

    Raises `PytestFailed` if pytest did not get far enough to have an answer.
    """
    command = list(pytest_command or default_pytest_command())
    probe_source = Path(__file__).with_name("_probe.py")

    with tempfile.TemporaryDirectory(prefix="uncollected-") as tmp:
        shutil.copyfile(probe_source, Path(tmp) / f"{PROBE_MODULE}.py")
        out = Path(tmp) / "probe.json"

        env = dict(os.environ)
        env["UNCOLLECTED_PROBE_OUT"] = str(out)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{tmp}{os.pathsep}{existing}" if existing else tmp

        try:
            completed = subprocess.run(
                [*command, "--collect-only", "-q", "-p", PROBE_MODULE],
                cwd=str(path),
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except FileNotFoundError as exc:
            raise PytestFailed(f"could not run {command[0]}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PytestFailed("pytest did not finish collecting within 600s") from exc

        if not out.exists():
            raise PytestFailed(
                _explain(command, completed, "pytest never finished collecting")
            )
        if completed.returncode not in OK_EXIT_CODES:
            raise PytestFailed(
                _explain(
                    command,
                    completed,
                    f"pytest exited {completed.returncode} during collection",
                )
            )
        payload = json.loads(out.read_text(encoding="utf-8"))

    return Session(
        pytest_version=payload["pytest_version"],
        rootdir=Path(payload["rootdir"]),
        configfile=payload["configfile"],
        ini=payload["ini"],
        items=[Collected(**item) for item in payload["items"]],
        deselected=[Collected(**item) for item in payload.get("deselected", [])],
    )


def _explain(command: list[str], completed: subprocess.CompletedProcess, headline: str) -> str:
    """pytest's own last words, which say more than anything we could add."""
    tail = (completed.stdout or "") + (completed.stderr or "")
    lines = [line for line in tail.splitlines() if line.strip()][-15:]
    rendered = "\n".join(f"    {line}" for line in lines) or "    (no output)"
    return f"{headline}\n  ran: {' '.join(command)} --collect-only\n{rendered}"
