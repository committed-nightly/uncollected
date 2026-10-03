"""Reading the source for things that look like tests.

Everything here is a syntactic question — what did you write? — kept strictly
apart from the question of what pytest did with it. The two are compared in
`checks`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import match
from .session import DEFAULT_NORECURSEDIRS

#: Directories never walked. pytest's own defaults, plus the places a stale
#: copy of a test suite lives. A finding inside `build/` is noise.
SKIP_DIR_PATTERNS = (*DEFAULT_NORECURSEDIRS, "__pycache__", "*.egg-info", "site-packages")

#: Method and function names that were aliases for pytest's real fixtures until
#: pytest 8.0 removed them. pytest now never calls them and says nothing.
NOSE_HOOKS = ("setup", "teardown")

#: Decorators that mean "this is not a test, whatever it is called".
NOT_A_TEST_DECORATORS = ("fixture", "overload")


@dataclass(frozen=True)
class Candidate:
    """A function that a reader would call a test."""

    file: str
    lineno: int
    name: str
    class_path: tuple[str, ...]
    decorators: tuple[str, ...]
    shadowed_by: int | None
    has_assertion: bool

    @property
    def qualname(self) -> str:
        return ".".join((*self.class_path, self.name))

    @property
    def location(self) -> str:
        return f"{self.file}:{self.lineno}"


@dataclass(frozen=True)
class Hook:
    """A `setup`/`teardown` that looks like a fixture and is not one."""

    file: str
    lineno: int
    name: str
    class_path: tuple[str, ...]

    @property
    def qualname(self) -> str:
        return ".".join((*self.class_path, self.name))

    @property
    def location(self) -> str:
        return f"{self.file}:{self.lineno}"


@dataclass
class ClassInfo:
    """What matters about a class when asking whether pytest will collect it."""

    name: str
    class_path: tuple[str, ...]
    lineno: int
    has_init: bool
    test_attr_false: bool
    shadowed_by: int | None


@dataclass
class Module:
    """One scanned file."""

    file: str
    candidates: list[Candidate] = field(default_factory=list)
    classes: dict[tuple[str, ...], ClassInfo] = field(default_factory=dict)
    hooks: list[Hook] = field(default_factory=list)
    #: Names invoked explicitly somewhere in the file, as `self.setup()` or
    #: `setup()`. A hook the tests call themselves is not dead.
    called_names: set[str] = field(default_factory=set)
    #: Every name this file uses as a base class. A class whose name turns up
    #: here is somebody's mixin, and its tests run under the subclass.
    base_names: set[str] = field(default_factory=set)


class Unparseable(Exception):
    """A file that is not valid Python for the interpreter running this."""


def _decorator_name(node: ast.expr) -> str:
    """``pytest.mark.parametrize`` from the decorator expression."""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return f"{_decorator_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_not_a_test(decorators: tuple[str, ...]) -> bool:
    return any(d.split(".")[-1] in NOT_A_TEST_DECORATORS for d in decorators)


def _has_assertion(node: ast.AST) -> bool:
    """Does this function body contain something that could fail?

    Used only to tell a test from a helper in a file pytest never looks at, so
    it is deliberately generous: a bare `assert`, anything reached through
    `pytest.`, or a `unittest` assertion method.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Attribute):
            dotted = _decorator_name(child)
            if dotted.startswith("pytest.") or dotted.split(".")[-1].startswith("assert"):
                return True
        if isinstance(child, ast.Raise):
            return True
    return False


def _shadowing(body: list[ast.stmt]) -> dict[int, int]:
    """Map a definition's line to the line of the definition that replaces it.

    Only direct children of the scope are considered, so a `def` guarded by
    `if TYPE_CHECKING:` or an `except ImportError:` fallback is not treated as
    shadowing anything. Python keeps whichever binding runs last; for a module
    body read top to bottom that is the final definition of the name.
    """
    lines_by_name: dict[str, list[int]] = {}
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines_by_name.setdefault(statement.name, []).append(statement.lineno)

    shadowed: dict[int, int] = {}
    for lines in lines_by_name.values():
        if len(lines) < 2:
            continue
        winner = lines[-1]
        for line in lines[:-1]:
            shadowed[line] = winner
    return shadowed


def _called_names(tree: ast.AST) -> set[str]:
    """Every name called as `foo()` or `self.foo()` anywhere in the file."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _functions(body: list[ast.stmt]) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [s for s in body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _base_names(tree: ast.AST) -> set[str]:
    """Every name used as a base class anywhere in the file.

    This is what keeps the mixin pattern from being reported. A class like
    ``class SharedTests:`` matches no `python_classes` pattern and pytest
    ignores it -- but ``class TestThing(SharedTests, TestCase)`` inherits its
    methods, and pytest collects them there. The tests run; only the class they
    were typed into is not a test class.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Subscript):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
            elif isinstance(base, ast.Attribute):
                names.add(base.attr)
    return names


def scan_source(source: str, relpath: str, function_patterns: list[str]) -> Module:
    """Find the test-shaped functions in one file's source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Unparseable(f"{relpath}: {exc}") from exc

    module = Module(
        file=relpath,
        called_names=_called_names(tree),
        base_names=_base_names(tree),
    )

    def looks_like_a_test(name: str) -> bool:
        # The configured patterns decide what pytest collects, but a function
        # called `test_x` is a test in the reader's head whatever the config
        # says -- and that mismatch is itself one of the findings.
        return name.startswith("test") or match.any_name_matches(function_patterns, name) is not None

    def visit_scope(body: list[ast.stmt], class_path: tuple[str, ...], class_shadowed: int | None) -> None:
        shadowed = _shadowing(body)

        for function in _functions(body):
            decorators = tuple(_decorator_name(d) for d in function.decorator_list)
            own_shadow = shadowed.get(function.lineno)
            if function.name in NOSE_HOOKS and not decorators:
                module.hooks.append(
                    Hook(
                        file=relpath,
                        lineno=function.lineno,
                        name=function.name,
                        class_path=class_path,
                    )
                )
            if not looks_like_a_test(function.name) or _is_not_a_test(decorators):
                continue
            module.candidates.append(
                Candidate(
                    file=relpath,
                    lineno=function.lineno,
                    name=function.name,
                    class_path=class_path,
                    decorators=decorators,
                    shadowed_by=own_shadow if own_shadow is not None else class_shadowed,
                    has_assertion=_has_assertion(function),
                )
            )

        for klass in [s for s in body if isinstance(s, ast.ClassDef)]:
            path = (*class_path, klass.name)
            inherited = shadowed.get(klass.lineno)
            if inherited is None:
                inherited = class_shadowed
            module.classes[path] = ClassInfo(
                name=klass.name,
                class_path=path,
                lineno=klass.lineno,
                has_init=any(f.name == "__init__" for f in _functions(klass.body)),
                test_attr_false=_declares_not_a_test(klass),
                shadowed_by=inherited,
            )
            visit_scope(klass.body, path, inherited)

    visit_scope(tree.body, (), None)
    return module


def _declares_not_a_test(klass: ast.ClassDef) -> bool:
    """Is there a ``__test__ = False`` in the class body?

    pytest's documented way of saying "do not collect me". Opting out on
    purpose is not a finding.
    """
    for statement in klass.body:
        targets = []
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__test__":
                value = statement.value
                return isinstance(value, ast.Constant) and value.value is False
    return False


def is_test_shaped(relpath: str, file_patterns: list[str]) -> bool:
    """Would a reader expect tests in this file?

    Wider than ``python_files`` on purpose: a file pytest does not collect is
    the interesting case, and this has to notice it to report it.
    """
    if match.any_path_matches(file_patterns, relpath) is not None:
        return True
    path = PurePosixPath(relpath)
    if any(part in ("test", "tests") for part in path.parts[:-1]):
        return True
    stem = path.name
    return stem.startswith("test") or stem.endswith("_test.py")


def walk(root: Path, file_patterns: list[str], function_patterns: list[str]) -> tuple[list[Module], list[str]]:
    """Scan every test-shaped file under `root`.

    Returns the modules and the paths that could not be parsed.
    """
    modules: list[Module] = []
    unparseable: list[str] = []

    for path in sorted(root.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        if _skipped(relpath):
            continue
        if not is_test_shaped(relpath, file_patterns):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unparseable.append(f"{relpath}: {exc}")
            continue
        try:
            modules.append(scan_source(source, relpath, function_patterns))
        except Unparseable as exc:
            unparseable.append(str(exc))

    return modules, unparseable


def _skipped(relpath: str) -> bool:
    parts = PurePosixPath(relpath).parts[:-1]
    for i in range(len(parts)):
        ancestor = "/".join(parts[: i + 1])
        if match.any_path_matches(list(SKIP_DIR_PATTERNS), ancestor) is not None:
            return True
    return False
