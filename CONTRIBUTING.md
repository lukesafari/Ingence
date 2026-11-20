# Contributing to Ingence

You can change your own copy of Ingence freely. This page says how to set up a clone, test a change
and propose it. Your work is released under the project's MIT license ([LICENSE](LICENSE)).

## Set up

You need Python 3.12 or newer and `git`. On Windows, use `.venv\Scripts\` for `.venv/bin/`.

```
git clone https://github.com/andrewbazley/ingence.git
cd ingence
```

```
python -m venv .venv
.venv/bin/python -m pip install -c tools/ci-constraints.txt -e ".[dev,content,browser]"
.venv/bin/python -m playwright install chromium
```

The `-c` file holds the tools at the versions the committed schemas, the lint and the type
check were made with.

## Test and check

```
.venv/bin/python -m pytest                                  # all of them
.venv/bin/python -m pytest tests/test_dictionary.py -x -q   # one file
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy src/ingence
npm install && npx tsc --noEmit -p tsconfig.json            # the Dashboard's scripts
```

The `test_browser` files drive Chromium and skip without it. To use another browser, run
`.venv/bin/python -m playwright install firefox webkit` and set `INGENCE_BROWSER=firefox` (or
`webkit`). A new browser test starts its browser with `launch_browser` in
`tests/fixtures/dashboard_browser.py`. The accessibility check reads axe-core from `node_modules`
(`npm install`) and skips without it.

## Regenerate what is committed

A test fails when one of these is stale. Each takes `--check`.

- `tools/export_schemas.py` writes `schemas/`. Run it after you change a record's definition.
- `tools/generate_web_types.py` writes the Dashboard's type declarations. Run it after the schemas change.
- `tools/write_pack_digests.py` writes a pack's digest. Run it after you change a pack's words, and
  raise the pack's `version` by hand.

After you change `explorer/`, run `npm ci` and `npm run build` there and commit what changes under
`src/ingence/web/explorer/`. The pull request checks rebuild it and fail where the committed files
differ.

## The oldest dependencies

`pyproject.toml` gives each dependency a lower bound, and an install at exactly those versions must
work. `tools/minimum-constraints.txt` pins every bound, and `tests/test_minimum_dependencies.py`
fails where the two disagree. To try the oldest install, use Python 3.12 in a fresh environment:

```
python -m venv .venv-min
.venv-min/bin/python -m pip install -c tools/minimum-constraints.txt -e ".[content]"
.venv-min/bin/python -m pip install pytest hypothesis
.venv-min/bin/python tools/smoke_installed.py --command .venv-min/bin/ingence
.venv-min/bin/python -m pytest --ignore-glob="tests/test_browser*"
```

Raise a bound only to a version you have run this way, and change both files in the same commit.

## What runs on a pull request

`.github/workflows/checks.yml` runs the checks above on every pull request: lint and types, the
generated files, the whole test suite with the browser tests required (a missing browser fails them,
it never skips them), the Dashboard's type check and JavaScript tests, the Graph workspace rebuilt
and compared, the oldest dependencies, and the built wheel installed in an empty environment and run
(`tools/smoke_installed.py`). A version tag, or a run started by hand from the Actions tab, also runs
the tests on Linux, macOS and Windows with the oldest and newest Python the package supports.

## Propose a change

Open an [issue][issues] first and say what you want to change and why. Use the issue forms for an
unknown word, a rule that reads a name wrongly, or a pack. For a question, start a
[discussion][discussions]. Then open a pull request that links the issue, with a test, and say how
you tested it.

Before you post a file or an output, see [Reporting a problem without sharing your data][scrub-guide].
A pack file records who answered and may hold words only your lab uses.

Some things stay fixed. Source data is read-only. A fact and an inference are never stored together.
Every conclusion can be reproduced. Not knowing is a valid answer. More evidence never lowers a
conclusion's standing. No model writes a conclusion. Everything runs on your own machine. A change
that breaks one of these will not be accepted.

[discussions]: https://github.com/andrewbazley/ingence/discussions
[issues]: https://github.com/andrewbazley/ingence/issues
[scrub-guide]: https://ingence.org/guides/reporting-a-problem.html
