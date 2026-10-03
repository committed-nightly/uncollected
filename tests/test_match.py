"""pytest's matching rules, which are not one rule.

If any of these are wrong the whole tool is wrong in the loudest possible way --
`python_classes = ["Test"]` failing to match `TestThing` would report every
class-based test in a repository as never collected.
"""

from __future__ import annotations

import pytest

from uncollected import match


class TestNameMatching:
    """`python_classes` and `python_functions`: prefix first, glob only if it looks like one."""

    @pytest.mark.parametrize(
        "pattern,name,expected",
        [
            ("Test", "TestThing", True),
            ("Test", "Test", True),
            ("Test", "MyTestThing", False),
            ("test", "test_thing", True),
            # pytest's prefix rule really is this blunt.
            ("test", "testify", True),
            ("test", "check_thing", False),
            # A glob pattern stops being a prefix.
            ("*Suite", "MySuite", True),
            ("*Suite", "SuiteMy", False),
            ("check_*", "check_one", True),
            ("check_*", "test_one", False),
        ],
    )
    def test_name_matches(self, pattern: str, name: str, expected: bool) -> None:
        assert match.name_matches(pattern, name) is expected

    def test_any_name_matches_returns_the_pattern_that_did_it(self) -> None:
        assert match.any_name_matches(["check_*", "Test"], "TestThing") == "Test"
        assert match.any_name_matches(["check_*", "Test"], "nothing") is None


class TestPathMatching:
    """`python_files` and `norecursedirs`: basename unless the pattern has a separator."""

    @pytest.mark.parametrize(
        "pattern,relpath,expected",
        [
            ("test_*.py", "test_x.py", True),
            # No separator in the pattern, so only the basename is considered --
            # which is why the default finds tests at any depth.
            ("test_*.py", "tests/deep/test_x.py", True),
            ("test_*.py", "tests/helpers.py", False),
            ("*_test.py", "tests/thing_test.py", True),
            # A separator in the pattern anchors it to the relative path.
            ("tests/test_*.py", "tests/test_x.py", True),
            ("tests/test_*.py", "other/test_x.py", False),
        ],
    )
    def test_path_matches(self, pattern: str, relpath: str, expected: bool) -> None:
        assert match.path_matches(pattern, relpath) is expected

    def test_any_path_matches_returns_the_pattern_that_did_it(self) -> None:
        patterns = ["test_*.py", "*_test.py"]
        assert match.any_path_matches(patterns, "a/b_test.py") == "*_test.py"
        assert match.any_path_matches(patterns, "a/helpers.py") is None


class TestDirectoryExclusion:
    """One matching ancestor is enough, because pytest tests each directory as it walks."""

    def test_the_immediate_parent(self) -> None:
        assert match.dir_is_excluded(["legacy"], "legacy/test_x.py") == "legacy"

    def test_a_grandparent(self) -> None:
        assert match.dir_is_excluded(["legacy"], "legacy/deep/test_x.py") == "legacy"

    def test_a_directory_further_down(self) -> None:
        assert match.dir_is_excluded(["fixtures"], "tests/fixtures/test_x.py") == "fixtures"

    def test_the_file_itself_is_not_a_directory(self) -> None:
        """`norecursedirs` is about directories; a file named `legacy` is not one."""
        assert match.dir_is_excluded(["legacy"], "legacy") is None

    def test_no_match(self) -> None:
        assert match.dir_is_excluded(["legacy"], "tests/test_x.py") is None

    def test_a_glob_pattern(self) -> None:
        assert match.dir_is_excluded(["*.egg"], "thing.egg/test_x.py") == "*.egg"


class TestUnderAny:
    """`testpaths`, which may name a directory or a file."""

    def test_inside_a_directory(self) -> None:
        assert match.under_any(["tests"], "tests/test_x.py")

    def test_deeper_inside_a_directory(self) -> None:
        assert match.under_any(["tests"], "tests/unit/test_x.py")

    def test_outside(self) -> None:
        assert not match.under_any(["tests"], "integration/test_x.py")

    def test_a_named_file(self) -> None:
        assert match.under_any(["tests/test_x.py"], "tests/test_x.py")

    def test_a_prefix_that_is_not_a_path_component(self) -> None:
        """`test` must not swallow `tests/`."""
        assert not match.under_any(["test"], "tests/test_x.py")

    def test_the_root_covers_everything(self) -> None:
        assert match.under_any(["."], "anywhere/test_x.py")
