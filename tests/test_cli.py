"""Arguments, exit codes, and the ways this can fail to have an answer."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from conftest import Project

from uncollected import cli


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestExitCodes:
    def test_clean_is_zero(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    assert True\n")
        code, _ = project.run()
        assert code == 0

    def test_findings_are_one(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    pass\n\ndef test_a():\n    pass\n")
        code, _ = project.run()
        assert code == 1

    def test_a_path_that_is_not_a_directory_is_two(self, tmp_path: Path) -> None:
        target = tmp_path / "nope"
        code, _, err = run_cli(str(target))
        assert code == 2
        assert "not a directory" in err


class TestCheckSelection:
    def test_list_checks(self) -> None:
        code, out, _ = run_cli("--list-checks")
        assert code == 0
        assert "shadowed" in out.split()
        assert "uncalled-hook" in out.split()

    def test_an_unknown_check_name_exits_two(self, tmp_path: Path) -> None:
        """Not 1 -- that is the code reserved for findings."""
        code, _, err = run_cli(str(tmp_path), "--only", "shadowd")
        assert code == 2
        assert "no such check: shadowd" in err
        assert "known checks:" in err

    def test_only_narrows_the_run(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    pass\n\ndef test_a():\n    pass\n")
        project.write("tests/helpers.py", "def test_helper():\n    assert False\n")
        assert {c for c, _ in project.checks()} == {"shadowed", "not-a-test-file"}
        assert {c for c, _ in project.checks("--only", "shadowed")} == {"shadowed"}

    def test_skip_removes_one(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    pass\n\ndef test_a():\n    pass\n")
        project.write("tests/helpers.py", "def test_helper():\n    assert False\n")
        assert {c for c, _ in project.checks("--skip", "shadowed")} == {"not-a-test-file"}


class TestWhenPytestHasNoAnswer:
    """A collection error is pytest's finding to report, not ours."""

    def test_a_test_module_that_cannot_be_imported(self, project: Project) -> None:
        project.write(
            "tests/test_broken.py",
            """
            import a_module_that_is_not_installed_anywhere

            def test_a():
                assert True
            """,
        )
        code, _, err = run_cli(str(project.root))
        assert code == 2
        assert "pytest exited" in err
        # pytest's own words, so the fix is obvious from the output.
        assert "a_module_that_is_not_installed_anywhere" in err

    def test_a_pytest_that_does_not_exist(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    assert True\n")
        code, _, err = run_cli(str(project.root), "--pytest", "definitely-not-a-real-pytest")
        assert code == 2
        assert "could not run" in err

    def test_an_empty_project_is_clean_not_an_error(self, project: Project) -> None:
        """pytest exits 5 for "no tests collected", which is a real answer."""
        code, out = project.run()
        assert code == 0
        assert "0 test(s) collected" in out


class TestUnreadableFiles:
    def test_a_helper_that_is_not_valid_python_is_named_not_fatal(
        self, project: Project
    ) -> None:
        """It does not match `python_files`, so pytest never tried to import it."""
        project.write("tests/test_a.py", "def test_a():\n    assert True\n")
        project.write("tests/broken_helper.py", "def (:\n")
        code, payload = project.findings()
        assert code == 0
        assert len(payload["unreadable"]) == 1
        assert "broken_helper.py" in payload["unreadable"][0]


class TestOutput:
    def test_the_headline_names_the_pytest_and_the_config_file(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    assert True\n")
        _, out = project.run()
        assert "pytest " in out
        assert "pytest.ini" in out
        assert "1 definition(s) look like tests, 1 test(s) collected" in out

    def test_json_is_valid_and_carries_the_config_it_used(self, project: Project) -> None:
        project.write("tests/test_a.py", "def test_a():\n    pass\n\ndef test_a():\n    pass\n")
        code, payload = project.findings()
        assert code == 1
        assert payload["ini"]["python_files"] == ["test_*.py", "*_test.py"]
        assert payload["findings"][0]["check"] == "shadowed"
        assert set(payload) >= {"collected", "candidates", "findings", "rootdir", "unreadable"}


class TestMissingPytest:
    def test_an_interpreter_without_pytest_says_so_plainly(self, project: Project) -> None:
        """The first thing a new user hits, since pytest is not a dependency."""
        project.write("tests/test_a.py", "def test_a():\n    assert True\n")
        # `-S` skips site-packages, so this interpreter cannot import pytest
        # whatever is installed. Deterministic where naming a system python
        # would depend on the machine.
        code, _, err = run_cli(
            str(project.root), "--pytest", f"{sys.executable} -S -m pytest"
        )
        assert code == 2
        assert "no pytest in that interpreter" in err
        assert "--pytest" in err
