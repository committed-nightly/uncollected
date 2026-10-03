"""Claims about pytest, checked against pytest.

The checks in this repository are only as good as its model of what pytest does,
and a unit test of that model only proves the model is self-consistent. These
run the real pytest and assert on what it actually collected, so that a change
in pytest's behaviour breaks a test here rather than quietly turning a finding
into a lie.
"""

from __future__ import annotations

import pytest
from conftest import Project


def collected(project: Project) -> int:
    _, payload = project.findings()
    return payload["collected"]


def test_a_duplicate_definition_is_collected_once_and_pytest_says_nothing(
    project: Project,
) -> None:
    """The whole reason this repository exists.

    Two tests written, one collected, no warning, and the suite passes -- so the
    test count is the only evidence and it looks entirely reasonable.
    """
    project.write(
        "tests/test_dup.py",
        """
        def test_alpha():
            assert True

        def test_alpha():
            assert True
        """,
    )
    assert collected(project) == 1


def test_python_classes_matches_by_prefix_not_by_substring(project: Project) -> None:
    project.write(
        "tests/test_classes.py",
        """
        class TestPrefixed:
            def test_a(self):
                assert True

        class SuffixedTest:
            def test_b(self):
                assert True
        """,
    )
    # `Test` prefix-matches the first and not the second, so one of the two
    # classes is collected.
    assert collected(project) == 1
    # Anchored on the test that never runs, not on the class, because the test
    # is the thing somebody thinks they have.
    assert project.checks() == [("not-a-test-class", "tests/test_classes.py:6")]


def test_python_functions_prefix_rule_catches_more_than_test_underscore(
    project: Project,
) -> None:
    """`python_functions = ["test"]` is a prefix, so `testify` is collected too."""
    project.write(
        "tests/test_names.py",
        """
        def testify():
            assert True
        """,
    )
    assert collected(project) == 1
    assert project.checks() == []


def test_a_class_with_an_init_is_warned_about_by_pytest_itself(
    project: Project, tmp_path
) -> None:
    """Checked here because this repository stays silent on the strength of it."""
    project.write(
        "tests/test_init.py",
        """
        class TestThing:
            def __init__(self):
                self.x = 1

            def test_inside(self):
                assert True
        """,
    )
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert "PytestCollectionWarning" in completed.stdout
    assert "TestThing" in completed.stdout


def test_nose_style_setup_is_not_called_on_pytest_8_or_newer(
    project: Project, tmp_path
) -> None:
    """And nothing is printed about it, which is why it is a check here."""
    if int(pytest.__version__.split(".")[0]) < 8:  # pragma: no cover
        pytest.skip("pytest 7 still calls the nose-style aliases")

    project.write(
        "tests/test_hook.py",
        """
        calls = []

        class TestThing:
            def setup(self):
                calls.append("setup")

            def test_it_ran(self):
                assert calls == ["setup"]
        """,
    )
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, "setup() was called after all"
    assert "warning" not in completed.stdout.lower().replace("warnings summary", "")


def test_norecursedirs_does_not_exclude_a_directory_named_in_testpaths(
    project: Project,
) -> None:
    """`norecursedirs` governs recursion, not the arguments pytest starts from.

    A directory listed in `testpaths` is an argument, so pytest collects it even
    while `norecursedirs` names it. Reporting those tests as never collected
    would be a confident lie, and it is the shape of mistake that is easy to
    make from reading the documentation rather than running it.
    """
    project.config(
        """
        norecursedirs = legacy
        testpaths = tests legacy
        """
    )
    project.write(
        "legacy/test_old.py",
        """
        def test_old():
            assert True
        """,
    )
    project.write(
        "tests/test_new.py",
        """
        def test_new():
            assert True
        """,
    )
    _, payload = project.findings()
    assert payload["collected"] == 2, "pytest collected the excluded directory anyway"
    assert payload["findings"] == []


def test_deselection_removes_items_from_the_session(project: Project) -> None:
    """Which is why `pytest_deselected` has to be recorded separately."""
    project.config(
        """
        addopts = -m "not slow"
        markers =
            slow: takes a while
        """
    )
    project.write(
        "tests/test_marked.py",
        """
        import pytest

        @pytest.mark.slow
        def test_slow():
            assert True

        def test_fast():
            assert True
        """,
    )
    _, payload = project.findings()
    assert payload["collected"] == 1
    assert payload["deselected"] == 1


def test_getini_reports_the_config_file_that_won(project: Project, tmp_path) -> None:
    """pytest.ini beats pyproject.toml, and the loser contributes nothing.

    This repository reads `python_files` from whichever file won rather than
    merging them, because pytest does not merge them either.
    """
    project.config("python_files = check_*.py\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npython_files = ["other_*.py"]\n', encoding="utf-8"
    )
    project.write(
        "tests/check_thing.py",
        """
        def test_a():
            assert True
        """,
    )
    project.write(
        "tests/other_thing.py",
        """
        def test_b():
            assert True
        """,
    )
    _, payload = project.findings()
    assert payload["configfile"].endswith("pytest.ini")
    assert payload["ini"]["python_files"] == ["check_*.py"]
    # `other_*.py` lost, so the test in it never runs.
    assert payload["collected"] == 1
    assert ("not-a-test-file", "tests/other_thing.py:1") in [
        (f["check"], f["location"]) for f in payload["findings"]
    ]
