"""pytest's own name-matching rules, reimplemented.

Three different rules are in play and they are not the same rule, which is the
reason this file exists rather than a couple of `fnmatch` calls at the call
site:

* ``python_files`` and ``norecursedirs`` match *paths*, with the wrinkle that a
  pattern containing a separator matches the whole relative path and one
  without matches only the basename.
* ``python_classes`` and ``python_functions`` match *names*, by **prefix** —
  the default ``python_classes = ["Test"]`` matches ``TestThing`` because of
  ``str.startswith``, not because of a glob. A pattern is only treated as a
  glob if it contains one of ``*?[``.

Getting the second one wrong is how a checker ends up reporting every test in
the repository as uncollected. The rules follow ``_pytest.pathlib.fnmatch_ex``
and ``_pytest.python.PyCollector._matches_prefix_or_glob_option``.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

#: Characters that make a ``python_classes``/``python_functions`` pattern a
#: glob rather than a prefix. Straight from pytest.
GLOB_CHARS = "*?["


def path_matches(pattern: str, relpath: str) -> bool:
    """Does `pattern` match `relpath` the way pytest matches a path pattern?

    `relpath` is a repository-relative POSIX path. A pattern with no separator
    is matched against the basename only, which is why the default
    ``test_*.py`` finds ``tests/deep/test_x.py``.
    """
    if "/" in pattern:
        return fnmatch.fnmatch(relpath, pattern)
    return fnmatch.fnmatch(PurePosixPath(relpath).name, pattern)


def any_path_matches(patterns: list[str], relpath: str) -> str | None:
    """The first pattern in `patterns` that matches `relpath`, or None."""
    for pattern in patterns:
        if path_matches(pattern, relpath):
            return pattern
    return None


def name_matches(pattern: str, name: str) -> bool:
    """Does `pattern` match `name` the way pytest matches a name pattern?

    Prefix first, glob only if the pattern looks like one. Note that this means
    ``python_functions = ["test"]`` matches ``testify`` as well as
    ``test_thing``: pytest's own behaviour, not a shortcut taken here.
    """
    if name.startswith(pattern):
        return True
    return any(c in pattern for c in GLOB_CHARS) and fnmatch.fnmatch(name, pattern)


def any_name_matches(patterns: list[str], name: str) -> str | None:
    """The first pattern in `patterns` that matches `name`, or None."""
    for pattern in patterns:
        if name_matches(pattern, name):
            return pattern
    return None


def dir_is_excluded(patterns: list[str], relpath: str) -> str | None:
    """The `norecursedirs` pattern that stops pytest descending to `relpath`.

    pytest tests each directory as it walks, so a pattern matching any ancestor
    of a file is enough to keep the file from ever being collected. Returns the
    pattern and the directory it matched, or None.
    """
    parts = PurePosixPath(relpath).parts[:-1]
    for i in range(len(parts)):
        ancestor = "/".join(parts[: i + 1])
        matched = any_path_matches(patterns, ancestor)
        if matched is not None:
            return matched
    return None


def under_any(roots: list[str], relpath: str) -> bool:
    """Is `relpath` inside any of `roots` (themselves relative POSIX paths)?

    Used for ``testpaths``, where a root may also name a file directly.
    """
    target = PurePosixPath(relpath)
    for root in roots:
        candidate = PurePosixPath(root)
        if target == candidate:
            return True
        if candidate == PurePosixPath("."):
            return True
        if candidate in target.parents:
            return True
    return False
