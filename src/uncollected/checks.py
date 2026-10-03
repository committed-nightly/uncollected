"""Comparing what you wrote against what pytest collected, and saying why.

The diff on its own is not worth much: "pytest did not collect this" is a fact
anybody can get from `--collect-only`. The reason is the product. A finding
without a reason is reported as `unexplained` rather than quietly dropped,
because a checker that hides the cases it does not understand is worse than one
that admits to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import match
from .scan import Module
from .session import DEFAULT_NORECURSEDIRS, Session

#: Every check, in the order findings are reported.
ALL_CHECKS = (
    "shadowed",
    "not-a-test-file",
    "unrecursed-dir",
    "outside-testpaths",
    "not-a-test-class",
    "not-a-test-function",
    "uncalled-hook",
    "unexplained",
)

#: The first pytest that stopped calling the nose-style `setup`/`teardown`
#: aliases. On 7.x they still run, with a deprecation warning, so the check
#: would be wrong there.
NOSE_REMOVED_IN_MAJOR = 8


@dataclass(frozen=True)
class Finding:
    check: str
    location: str
    message: str
    detail: tuple[str, ...] = ()


def run(
    modules: list[Module],
    session: Session,
    root: Path,
    enabled: set[str] | None = None,
) -> list[Finding]:
    """All findings for one scan, ordered by check then by location."""
    enabled = set(ALL_CHECKS) if enabled is None else enabled
    findings = [
        *_uncollected(modules, session, root),
        *_uncalled_hooks(modules, session),
    ]
    findings = [f for f in findings if f.check in enabled]
    order = {check: i for i, check in enumerate(ALL_CHECKS)}
    return sorted(findings, key=lambda f: (order[f.check], f.location))


def _configured_norecursedirs(session: Session) -> list[str]:
    """Only the patterns the project added.

    pytest's defaults exclude `build`, `dist`, `.*` and friends. A test file
    under one of those is a stale copy, not a test somebody is waiting on.
    """
    return [p for p in session.opt("norecursedirs") if p not in DEFAULT_NORECURSEDIRS]


def _testpath_roots(session: Session, root: Path) -> list[str]:
    """`testpaths`, with any globs expanded against the rootdir.

    pytest 8 allows globs here. An unexpanded `tests/*` would match nothing and
    make every file in the project look out of scope.
    """
    roots: list[str] = []
    for entry in session.opt("testpaths"):
        if any(c in entry for c in match.GLOB_CHARS):
            roots.extend(p.relative_to(root).as_posix() for p in sorted(root.glob(entry)))
        else:
            roots.append(entry.rstrip("/"))
    return roots


def _file_reason(relpath: str, session: Session, root: Path) -> tuple[str, str] | None:
    """Why pytest never looks inside this file at all, if it does not."""
    file_patterns = session.opt("python_files")
    if match.any_path_matches(file_patterns, relpath) is None:
        return (
            "not-a-test-file",
            f"the filename matches no python_files pattern ({', '.join(file_patterns)}), "
            f"so pytest never imports it",
        )

    excluded_by = match.dir_is_excluded(_configured_norecursedirs(session), relpath)
    if excluded_by is not None:
        return (
            "unrecursed-dir",
            f"a directory above it matches norecursedirs pattern {excluded_by!r}, "
            f"so pytest never descends to it",
        )

    roots = _testpath_roots(session, root)
    if roots and not match.under_any(roots, relpath):
        return (
            "outside-testpaths",
            f"testpaths is {', '.join(roots)}, and this file is outside it, "
            f"so a bare `pytest` never reaches it",
        )
    return None


def _uncollected(modules: list[Module], session: Session, root: Path) -> list[Finding]:
    collected = session.collected_keys
    findings: list[Finding] = []

    for module in modules:
        file_reason = _file_reason(module.file, session, root)

        for candidate in module.candidates:
            if (candidate.file, candidate.qualname) in collected:
                continue
            if _opted_out(candidate.class_path, module):
                continue
            if _pytest_already_warned(candidate.class_path, module, session):
                continue

            if candidate.shadowed_by is not None:
                findings.append(
                    Finding(
                        check="shadowed",
                        location=candidate.location,
                        message=(
                            f"{candidate.qualname} is replaced by the definition at "
                            f"line {candidate.shadowed_by} of the same file, so this "
                            f"body never runs and the test count does not change"
                        ),
                    )
                )
                continue

            if file_reason is not None:
                check, message = file_reason
                # In a file pytest never reads, `test_data` returning a dict is
                # a helper and `test_data` asserting something is a test. Only
                # the second is worth anybody's morning.
                if check == "not-a-test-file" and not candidate.has_assertion:
                    continue
                findings.append(
                    Finding(
                        check=check,
                        location=candidate.location,
                        message=f"{candidate.qualname} never runs: {message}",
                    )
                )
                continue

            class_reason = _class_reason(candidate.class_path, session)
            if class_reason is not None:
                findings.append(
                    Finding(
                        check="not-a-test-class",
                        location=candidate.location,
                        message=f"{candidate.qualname} never runs: {class_reason}",
                    )
                )
                continue

            function_patterns = session.opt("python_functions")
            if match.any_name_matches(function_patterns, candidate.name) is None:
                findings.append(
                    Finding(
                        check="not-a-test-function",
                        location=candidate.location,
                        message=(
                            f"{candidate.qualname} never runs: the name matches no "
                            f"python_functions pattern "
                            f"({', '.join(function_patterns)})"
                        ),
                    )
                )
                continue

            findings.append(
                Finding(
                    check="unexplained",
                    location=candidate.location,
                    message=(
                        f"{candidate.qualname} looks like a test, pytest did not "
                        f"collect it, and this does not know why -- please report it"
                    ),
                )
            )

    return findings


def _opted_out(class_path: tuple[str, ...], module: Module) -> bool:
    """Did somebody write ``__test__ = False`` on an enclosing class?

    That is pytest's documented way of saying "not a test". Saying so and then
    being told about it would be the tool arguing with its user.
    """
    for depth in range(len(class_path)):
        info = module.classes.get(class_path[: depth + 1])
        if info is not None and info.test_attr_false:
            return True
    return False


def _pytest_already_warned(
    class_path: tuple[str, ...], module: Module, session: Session
) -> bool:
    """Is pytest itself going to mention this one?

    A class that matches `python_classes` but has an `__init__` gets a
    PytestCollectionWarning naming the class and the file. pytest's warning is
    better than anything this could add, so it is left alone.
    """
    patterns = session.opt("python_classes")
    for depth in range(len(class_path)):
        info = module.classes.get(class_path[: depth + 1])
        if info is None:
            continue
        matches = match.any_name_matches(patterns, info.name) is not None
        if matches and info.has_init:
            return True
    return False


def _class_reason(class_path: tuple[str, ...], session: Session) -> str | None:
    """Why pytest skipped the class this test is sitting in."""
    patterns = session.opt("python_classes")
    for depth in range(len(class_path)):
        name = class_path[depth]
        if match.any_name_matches(patterns, name) is None:
            return (
                f"the enclosing class {'.'.join(class_path[: depth + 1])} matches no "
                f"python_classes pattern ({', '.join(patterns)}), so pytest ignores "
                f"the class and everything in it"
            )
    return None


def _uncalled_hooks(modules: list[Module], session: Session) -> list[Finding]:
    """`setup`/`teardown` that pytest 8 stopped calling and nothing announced."""
    if session.major < NOSE_REMOVED_IN_MAJOR:
        return []

    collected = session.collected_keys
    findings: list[Finding] = []

    for module in modules:
        for hook in module.hooks:
            # A method the tests call themselves is doing its job under a
            # confusing name, which is not this tool's business.
            if hook.name in module.called_names:
                continue

            if hook.class_path:
                prefix = ".".join(hook.class_path) + "."
                live = any(
                    file == module.file and qualname.startswith(prefix)
                    for file, qualname in collected
                )
                where = f"class {'.'.join(hook.class_path)}"
                replacement = f"{hook.name}_method"
            else:
                live = module.file in {file for file, _ in collected}
                where = "this module"
                replacement = f"{hook.name}_module"

            if not live:
                continue

            findings.append(
                Finding(
                    check="uncalled-hook",
                    location=hook.location,
                    message=(
                        f"{hook.qualname} is never called: pytest {session.pytest_version} "
                        f"removed the nose-style `{hook.name}` alias in 8.0, and tests in "
                        f"{where} run without it. Rename it to `{replacement}` or make it "
                        f"a fixture"
                    ),
                )
            )

    return findings
