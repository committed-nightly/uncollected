"""A pytest plugin that writes down what pytest actually collected.

This file is never imported by `uncollected` itself. It is copied into a
temporary directory and loaded with ``-p`` by the *target project's* pytest, in
whatever interpreter and virtualenv that happens to be, so it must import
nothing beyond the standard library and must not assume this package is on the
path.

It exists so that nothing here has to reimplement pytest's configuration
precedence. Asking ``config.getini`` is the only way to be right about which of
``pytest.ini``, ``pyproject.toml``, ``tox.ini`` and ``setup.cfg`` won, and about
what the resulting values are.
"""

import json
import os

#: The settings that decide whether a test gets collected at all.
WANTED_INI = (
    "python_files",
    "python_classes",
    "python_functions",
    "norecursedirs",
    "testpaths",
)


#: Node ids pytest collected and then threw away again, because a marker
#: expression in `addopts` or on the command line deselected them. They are not
#: uncollected and must not be reported as such.
_deselected = []


def pytest_deselected(items):
    for item in items:
        relpath, lineno, domain = _location(item)
        if relpath is None:
            continue
        _deselected.append(
            {"nodeid": item.nodeid, "file": relpath, "line": lineno, "domain": domain}
        )


def _location(item):
    """pytest's own idea of where an item is, as (relpath, 1-based line).

    ``item.location`` reports 0-based lines, and for a decorated function it
    reports the line of the *first decorator* rather than the ``def``. Both are
    fine for printing and neither is relied on for matching.
    """
    try:
        relpath, line, domain = item.location
    except Exception:
        return None, None, None
    lineno = line + 1 if isinstance(line, int) else None
    return str(relpath).replace(os.sep, "/"), lineno, domain


def pytest_collection_finish(session):
    config = session.config

    ini = {}
    for name in WANTED_INI:
        try:
            value = config.getini(name)
        except (ValueError, KeyError):
            continue
        ini[name] = [str(v) for v in value] if isinstance(value, (list, tuple)) else str(value)

    items = []
    for item in session.items:
        relpath, lineno, domain = _location(item)
        if relpath is None:
            continue
        items.append(
            {
                "nodeid": item.nodeid,
                "file": relpath,
                "line": lineno,
                "domain": domain,
            }
        )

    import pytest

    inipath = getattr(config, "inipath", None)
    payload = {
        "pytest_version": pytest.__version__,
        "rootdir": str(config.rootpath),
        "configfile": str(inipath) if inipath else None,
        "ini": ini,
        "items": items,
        "deselected": _deselected,
    }

    with open(os.environ["UNCOLLECTED_PROBE_OUT"], "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
