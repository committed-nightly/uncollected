# uncollected

Find the tests pytest never runs — the ones that are in the file, look
completely normal, and are not in the test count.

Python rebinds a duplicate name without a word, so a test file with two
`def test_alpha` has one test in it. pytest does not warn, the suite passes,
and the number at the bottom goes up every time you add a test, including that
one. There is nothing to notice.

That is one of seven reasons a test you wrote does not run. The others are
configuration: a file whose name matches no `python_files` pattern is never
imported, a class whose name matches no `python_classes` pattern is skipped
along with everything inside it, and a `norecursedirs` entry quietly removes a
directory. None of these are errors. Every one of them is silent.

Written for the moment you inherit a test suite and want to know whether the
green tick covers what you think it covers.

## Install

Python 3.10 or newer.

```
pip install git+https://github.com/committed-nightly/uncollected
```

## Usage

```
uncollected [PATH] [--pytest CMD] [--only CHECK] [--skip CHECK] [--json]
```

Exit status is `0` when pytest collects everything, `1` when it does not, and
`2` when pytest could not finish collecting and so has no opinion to report.

`uncollected` runs the project's own pytest. If the project lives in a
different virtualenv from this tool, point at it:

```
uncollected . --pytest '.venv/bin/python -m pytest'
```

## A real example

[`Textualize/rich`](https://github.com/Textualize/rich), at the time of
writing, with 981 tests collected and all of them passing:

```
$ uncollected .
pytest 9.1.1, rootdir /tmp/rich, /tmp/rich/pyproject.toml: 694 definition(s) look like tests, 981 test(s) collected

tests/test_console.py:48: shadowed: test_soft_wrap is replaced by the definition at line 604 of the same file, so this body never runs and the test count does not change
tests/test_console.py:953: shadowed: test_force_color is replaced by the definition at line 968 of the same file, so this body never runs and the test count does not change

2 test(s) you wrote that pytest never runs: 2 shadowed
```

Both hidden bodies still contain an assertion. Rename the first of each pair
so that both definitions can run, and:

```
$ pytest tests/test_console.py -k FIRST
E   assert 'foo foo foo ...oo foo foo \n' == 'foo foo foo ... foo foo foo '
E   assert False
E    +  where False = <console width=80 None>.is_terminal
2 failed, 2 passed
```

Whether those are product bugs or assertions that went stale is a question for
`rich`, and the point here is that nobody has had to answer it. The second is
the more expensive shape: the hidden `test_force_color` carries a
`@pytest.mark.parametrize` over three values of `FORCE_COLOR`, and the
definition that replaced it is not parametrised at all. Three cases went, the
decorator went with them, and the test count did not move.

## The checks

| check | what it means |
| --- | --- |
| `shadowed` | a later `def` or `class` of the same name in the same scope replaced this one, so Python never keeps this body |
| `not-a-test-file` | the filename matches no `python_files` pattern, so pytest never imports the file |
| `unrecursed-dir` | a directory above it matches a `norecursedirs` pattern **the project configured**, so pytest never descends to it |
| `outside-testpaths` | `testpaths` is set and this file is outside it, so a bare `pytest` never reaches it |
| `not-a-test-class` | the enclosing class matches no `python_classes` pattern, so pytest ignores the class and everything in it |
| `not-a-test-function` | the name matches no `python_functions` pattern |
| `uncalled-hook` | a nose-style `setup`/`teardown`, which pytest has not called since 8.0 |

`uncalled-hook` is the one that is not about a test. pytest 7 called a method
named `setup` before each test in the class and printed a deprecation warning;
pytest 8 removed it and prints nothing. A suite that upgraded has methods that
look like fixtures, read like fixtures, and do not run:

```
tests/test_thing.py:2: uncalled-hook: TestThing.setup is never called: pytest 9.1.1 removed the nose-style `setup` alias in 8.0, and tests in class TestThing run without it. Rename it to `setup_method` or make it a fixture
```

The check is skipped entirely on pytest 7, where those methods do still run.

## How it decides

The ground truth is pytest, not a model of pytest. `uncollected` runs
`pytest --collect-only` in the project with **no path arguments** — so
`testpaths`, `norecursedirs` and the project's own `addopts` all apply exactly
as they do in its CI — with a small plugin loaded via `-p`. The plugin reports
`session.items` and `config.getini(...)`.

That last part is the reason for a plugin rather than a config parser. pytest
reads `pytest.ini`, `pyproject.toml`, `tox.ini` and `setup.cfg` in a fixed
precedence and does **not** merge them: if `pytest.ini` exists, the
`[tool.pytest.ini_options]` table in `pyproject.toml` contributes nothing at
all. Asking `getini` is the only way to be right about which file won.

Separately, every test-shaped file is parsed and every `def` a reader would
call a test is written down. The two lists are compared on `(file, qualname)`,
and anything in the first and not the second gets a reason.

Three details that are easy to get wrong, each covered by a test here:

- **`python_classes` and `python_functions` match by prefix**, not by glob —
  `Test` matches `TestThing` through `str.startswith`. A pattern only becomes a
  glob if it contains one of `*?[`. Treating the default as a glob would report
  every class-based test in a repository as uncollected.
- **Shadowing is checked before asking whether pytest collected the name**,
  because the definition that replaced it has the *same* qualname. pytest did
  collect `test_alpha` — just not that one.
- **Deselected is not uncollected.** A test removed by `-m "not slow"` is
  configured, not lost, so the plugin records `pytest_deselected` separately.

If a test is uncollected and none of the reasons apply, it is reported as
`unexplained` rather than dropped. A checker that hides the cases it does not
understand is worse than one that admits to them.

### On files pytest never reads

In a file that matches no `python_files` pattern, `test_data` returning a dict
is a helper and `test_data` asserting something is a test, and only the second
is worth your morning. So in that one case a finding also requires the body to
contain an `assert`, a `raise`, or something reached through `pytest.`. In
files pytest *does* read, no such evidence is needed — the file is already
established as a test file.

## What it will not tell you

**It needs a pytest that can collect.** If a test module fails to import you
get pytest's error and nothing else, because the import error is the finding
and pytest has already printed it better than this could. This is the main
limitation: to audit a project you have to be able to install it.

**`ruff check --select F811` finds duplicate definitions too, and finds more of
them.** It sees shadowed fixtures and shadowed helpers; this only looks at
things named like tests. Run ruff. What ruff cannot tell you is that your whole
`checks.py` is never imported, because that is not a Python fact — it is a
pytest configuration fact, and answering it needs pytest's effective config.
(The argument for ruff across this org is
[logbook#36](https://github.com/committed-nightly/logbook/issues/36), and the
`allow_file` bug that prompted it is a shadowed *fixture* — exactly the case
this tool does not cover.)

**Anything pytest already warns about is left alone.** A class matching
`python_classes` with an `__init__` gets a `PytestCollectionWarning` naming the
class and the file; a test that returns instead of asserting gets its own
warning; a config file beaten by a higher-precedence one is named in the run
header. pytest's warning is better than anything this could add. One caveat,
since it is the quietest of the three: the config warning is printed in the run
*header*, which `-q` hides.

**A base class full of tests is assumed to be alive.** If a class's name is
used as a base anywhere in the scanned files, its tests are taken to run under
the subclass and no `not-a-test-class` finding is reported. This is the mixin
pattern — `more-itertools` and `websockets` both use it — and without the
suppression those two projects alone produce dozens of findings that are all
wrong. The cost is that a shared base class nobody actually subclasses is
missed.

**Only test-shaped files are scanned**: files matching `python_files`, files
under a `test`/`tests` directory, and files whose name starts with `test` or
ends with `_test.py`. A test hidden in `src/utils.py` is not looked for.

**Directories pytest excludes by default are not reported.** A copy of the
suite under `build/` or `.tox/` is an artefact, not a finding. Only
`norecursedirs` patterns the project added are reported.

## Tests

```
pip install -e ".[dev]"
python -m pytest
ruff check .
```

The suite builds real projects on disk and runs the real pytest against them,
so a change in pytest's behaviour breaks a test here rather than quietly
turning a finding into a lie. `tests/test_pytest_behaviour.py` is that on
purpose: it asserts what pytest does, not what this repository believes.

## Licence

MIT.
