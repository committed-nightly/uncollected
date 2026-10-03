"""Each check, against a project a real pytest has actually collected."""

from __future__ import annotations

import pytest

from conftest import Project


def test_a_clean_project_has_no_findings(project: Project) -> None:
    project.write(
        "tests/test_fine.py",
        """
        def test_one():
            assert True

        class TestGroup:
            def test_two(self):
                assert True
        """,
    )
    code, output = project.run()
    assert code == 0
    assert "pytest collects every test in this project" in output


def test_shadowed_definition_is_found_even_though_the_name_was_collected(
    project: Project,
) -> None:
    project.write(
        "tests/test_dup.py",
        """
        def test_alpha():
            assert False

        def test_alpha():
            assert True
        """,
    )
    code, payload = project.findings()
    assert code == 1
    assert payload["collected"] == 1
    assert payload["candidates"] == 2
    (finding,) = payload["findings"]
    assert finding["check"] == "shadowed"
    assert finding["location"] == "tests/test_dup.py:1"
    assert "line 4" in finding["message"]


def test_shadowed_method_inside_a_class(project: Project) -> None:
    project.write(
        "tests/test_dup.py",
        """
        class TestGroup:
            def test_beta(self):
                assert False

            def test_beta(self):
                assert True
        """,
    )
    assert project.checks() == [("shadowed", "tests/test_dup.py:2")]


def test_a_duplicate_class_takes_its_whole_body_with_it(project: Project) -> None:
    """The first class is rebound, so every test in it is gone at once."""
    project.write(
        "tests/test_dup.py",
        """
        class TestGroup:
            def test_one(self):
                assert False

            def test_two(self):
                assert False

        class TestGroup:
            def test_three(self):
                assert True
        """,
    )
    assert project.checks() == [
        ("shadowed", "tests/test_dup.py:2"),
        ("shadowed", "tests/test_dup.py:5"),
    ]


def test_a_definition_guarded_by_an_if_is_not_shadowing(project: Project) -> None:
    """Only direct children of a scope count, or every fallback import trips it."""
    project.write(
        "tests/test_guarded.py",
        """
        import sys

        if sys.version_info >= (3, 99):
            def test_alpha():
                assert True
        else:
            def test_alpha():
                assert True
        """,
    )
    assert project.checks() == []


def test_file_that_matches_no_python_files_pattern(project: Project) -> None:
    project.write(
        "tests/checks.py",
        """
        def test_important():
            assert 1 == 2
        """,
    )
    code, payload = project.findings()
    assert code == 1
    (finding,) = payload["findings"]
    assert finding["check"] == "not-a-test-file"
    assert "test_*.py, *_test.py" in finding["message"]


def test_a_helper_named_like_a_test_is_not_reported(project: Project) -> None:
    """`test_data` returning a dict in an uncollected file is a helper."""
    project.write(
        "tests/factories.py",
        """
        def test_data():
            return {"a": 1}

        def test_payload():
            return test_data()
        """,
    )
    project.write(
        "tests/test_real.py",
        """
        def test_real():
            assert True
        """,
    )
    assert project.checks() == []


def test_a_configured_norecursedir_hides_a_whole_tree(project: Project) -> None:
    project.config("norecursedirs = legacy\n")
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
    code, payload = project.findings()
    assert code == 1
    (finding,) = payload["findings"]
    assert finding["check"] == "unrecursed-dir"
    assert finding["location"] == "legacy/test_old.py:1"
    assert "'legacy'" in finding["message"]


def test_pytests_own_default_norecursedirs_are_not_reported(project: Project) -> None:
    """A test suite copied into `build/` is an artefact, not a finding."""
    project.write(
        "build/lib/tests/test_stale.py",
        """
        def test_stale():
            assert True
        """,
    )
    project.write(
        "tests/test_live.py",
        """
        def test_live():
            assert True
        """,
    )
    assert project.checks() == []


def test_testpaths_that_leaves_a_directory_out(project: Project) -> None:
    project.config("testpaths = tests\n")
    project.write(
        "integration/test_slow.py",
        """
        def test_slow():
            assert True
        """,
    )
    project.write(
        "tests/test_fast.py",
        """
        def test_fast():
            assert True
        """,
    )
    code, payload = project.findings()
    assert code == 1
    (finding,) = payload["findings"]
    assert finding["check"] == "outside-testpaths"
    assert finding["location"] == "integration/test_slow.py:1"


def test_a_globbed_testpath_is_expanded_before_being_believed(project: Project) -> None:
    """An unexpanded `*` would put every file outside testpaths."""
    project.config("testpaths = tests/*\n")
    project.write(
        "tests/unit/test_a.py",
        """
        def test_a():
            assert True
        """,
    )
    assert project.checks() == []


def test_class_that_matches_no_python_classes_pattern(project: Project) -> None:
    project.write(
        "tests/test_group.py",
        """
        class Helpers:
            def test_inside(self):
                assert True

        def test_outside():
            assert True
        """,
    )
    code, payload = project.findings()
    assert code == 1
    (finding,) = payload["findings"]
    assert finding["check"] == "not-a-test-class"
    assert finding["location"] == "tests/test_group.py:2"
    assert "Helpers" in finding["message"]


class TestSharedBaseClasses:
    """The mixin pattern, which is the easiest way to make this tool useless.

    A base class full of tests matches no `python_classes` pattern and pytest
    ignores the class -- but the subclass inherits the methods and pytest
    collects them there, under a different qualname. Reported as findings, the
    four in `more-itertools` and the dozens in `websockets` would be the first
    thing anyone saw.
    """

    def test_a_base_class_subclassed_in_the_same_file(self, project: Project) -> None:
        project.write(
            "tests/test_shared.py",
            """
            class PeekableMixinTests:
                def test_passthrough(self):
                    assert True

            class TestPeekable(PeekableMixinTests):
                pass
            """,
        )
        code, payload = project.findings()
        assert payload["collected"] == 1, "the inherited test should be collected"
        assert code == 0
        assert payload["findings"] == []

    def test_a_base_class_subclassed_in_another_file(self, project: Project) -> None:
        """Which is why the base names are unioned across the whole project."""
        project.write(
            "tests/base.py",
            """
            class SharedTests:
                def test_shared(self):
                    assert True
            """,
        )
        project.write(
            "tests/test_impl.py",
            """
            from base import SharedTests

            class TestImpl(SharedTests):
                pass
            """,
        )
        assert project.checks() == []

    def test_a_dotted_base_is_recognised(self, project: Project) -> None:
        """`class TestImpl(shared.SharedTests)` names the base as an attribute."""
        project.write(
            "tests/shared.py",
            """
            class SharedTests:
                def test_shared(self):
                    assert True
            """,
        )
        project.write(
            "tests/test_dotted.py",
            """
            import shared

            class TestImpl(shared.SharedTests):
                pass
            """,
        )
        code, payload = project.findings()
        assert payload["collected"] == 1
        assert payload["findings"] == []
        assert code == 0

    def test_a_class_nobody_inherits_is_still_reported(self, project: Project) -> None:
        """The suppression must not swallow the check it is protecting."""
        project.write(
            "tests/test_orphan.py",
            """
            class Helpers:
                def test_inside(self):
                    assert True

            def test_outside():
                assert True
            """,
        )
        assert project.checks() == [("not-a-test-class", "tests/test_orphan.py:2")]


def test_a_class_with_an_init_is_left_to_pytests_own_warning(project: Project) -> None:
    """pytest names the class and the file. Repeating it adds nothing."""
    project.write(
        "tests/test_init.py",
        """
        class TestThing:
            def __init__(self):
                self.x = 1

            def test_inside(self):
                assert True

        def test_outside():
            assert True
        """,
    )
    assert project.checks() == []


def test_test_attr_false_is_an_answer_not_a_finding(project: Project) -> None:
    project.write(
        "tests/test_optout.py",
        """
        class TestNotReally:
            __test__ = False

            def test_inside(self):
                assert True

        def test_outside():
            assert True
        """,
    )
    assert project.checks() == []


def test_python_functions_narrowed_so_a_name_stops_matching(project: Project) -> None:
    project.config("python_functions = check_*\n")
    project.write(
        "tests/test_names.py",
        """
        def check_one():
            assert True

        def test_two():
            assert True
        """,
    )
    code, payload = project.findings()
    assert code == 1
    (finding,) = payload["findings"]
    assert finding["check"] == "not-a-test-function"
    assert finding["location"] == "tests/test_names.py:4"


def test_a_fixture_named_like_a_test_is_not_a_test(project: Project) -> None:
    project.write(
        "tests/test_fix.py",
        """
        import pytest

        @pytest.fixture
        def test_thing():
            return 1

        def test_uses(test_thing):
            assert test_thing == 1
        """,
    )
    assert project.checks() == []


def test_parametrised_tests_are_matched_despite_the_decorator_line(
    project: Project,
) -> None:
    """pytest reports a decorated test at the decorator's line, not the def's."""
    project.write(
        "tests/test_param.py",
        """
        import pytest

        @pytest.mark.parametrize("n", [1, 2])
        def test_each(n):
            assert n
        """,
    )
    assert project.checks() == []


def test_deselected_tests_are_configured_not_lost(project: Project) -> None:
    """`-m "not slow"` removes items from the session. That is not a finding."""
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
    code, payload = project.findings()
    assert code == 0
    assert payload["deselected"] == 1
    assert payload["findings"] == []


def test_skipped_tests_are_still_collected(project: Project) -> None:
    project.write(
        "tests/test_skip.py",
        """
        import pytest

        @pytest.mark.skip(reason="not yet")
        def test_later():
            assert True
        """,
    )
    assert project.checks() == []


class TestNoseStyleHooks:
    """`setup`/`teardown` stopped being called in pytest 8.0, silently."""

    def test_an_uncalled_class_setup_is_reported(self, project: Project) -> None:
        project.write(
            "tests/test_hook.py",
            """
            class TestThing:
                def setup(self):
                    self.value = 1

                def test_uses_it(self):
                    assert True
            """,
        )
        code, payload = project.findings()
        if pytest.__version__.split(".")[0] < "8":  # pragma: no cover
            pytest.skip("pytest 7 still calls these")
        assert code == 1
        (finding,) = payload["findings"]
        assert finding["check"] == "uncalled-hook"
        assert finding["location"] == "tests/test_hook.py:2"
        assert "setup_method" in finding["message"]

    def test_a_module_level_setup_is_reported(self, project: Project) -> None:
        project.write(
            "tests/test_modhook.py",
            """
            def setup():
                pass

            def test_thing():
                assert True
            """,
        )
        assert project.checks("--only", "uncalled-hook") == [
            ("uncalled-hook", "tests/test_modhook.py:1")
        ]

    def test_setup_method_is_the_real_name_and_is_fine(self, project: Project) -> None:
        project.write(
            "tests/test_proper.py",
            """
            class TestThing:
                def setup_method(self):
                    self.value = 1

                def test_uses_it(self):
                    assert self.value == 1
            """,
        )
        assert project.checks() == []

    def test_a_setup_the_tests_call_themselves_is_doing_its_job(
        self, project: Project
    ) -> None:
        project.write(
            "tests/test_called.py",
            """
            class TestThing:
                def setup(self):
                    self.value = 1

                def test_uses_it(self):
                    self.setup()
                    assert self.value == 1
            """,
        )
        assert project.checks() == []

    def test_a_setup_on_a_class_pytest_ignores_is_not_reported(
        self, project: Project
    ) -> None:
        """The class is not collected, so nothing in it was going to run."""
        project.write(
            "tests/test_helper_class.py",
            """
            class Builder:
                def setup(self):
                    self.value = 1

            def test_thing():
                assert True
            """,
        )
        assert project.checks("--only", "uncalled-hook") == []

    def test_a_setup_decorated_as_a_fixture_is_a_fixture(self, project: Project) -> None:
        project.write(
            "tests/test_fixture_setup.py",
            """
            import pytest

            class TestThing:
                @pytest.fixture(autouse=True)
                def setup(self):
                    self.value = 1

                def test_uses_it(self):
                    assert self.value == 1
            """,
        )
        assert project.checks() == []
