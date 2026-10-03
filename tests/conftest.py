"""Fixture projects, checked by running the real pytest against them.

Every project gets a config file even when the test does not care about
configuration. Without one, pytest picks a rootdir by walking up from the
working directory looking for `setup.py` and friends, and under `/tmp` that
could land anywhere -- which would make these tests depend on what else is in
the temporary directory.
"""

from __future__ import annotations

import io
import json
import textwrap
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from uncollected import cli


@dataclass
class Project:
    root: Path

    def write(self, relpath: str, source: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
        return path

    def config(self, body: str = "") -> None:
        self.write("pytest.ini", "[pytest]\n" + textwrap.dedent(body).lstrip("\n"))

    def run(self, *args: str) -> tuple[int, str]:
        """The CLI's text output, exactly as a person would see it."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main([str(self.root), *args])
        return code, buffer.getvalue()

    def findings(self, *args: str) -> tuple[int, dict]:
        """The CLI's `--json` payload."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main([str(self.root), "--json", *args])
        return code, json.loads(buffer.getvalue())

    def checks(self, *args: str) -> list[tuple[str, str]]:
        """(check, location) for each finding, which is what most tests assert."""
        _, payload = self.findings(*args)
        return [(f["check"], f["location"]) for f in payload["findings"]]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    built = Project(root=tmp_path)
    built.config()
    return built
