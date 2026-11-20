"""Check that an installed Ingence works end to end: create a project, list a folder, run the rules.

This drives the ``ingence`` command a person runs, from wherever it was installed (a wheel in a
fresh environment, a minimum-dependency install), on a small invented folder. It is the check that
the package's metadata and its code agree. An install that satisfies ``pyproject.toml`` must be able
to read files, and a wheel must carry the Dictionary, the Rulebook and the Dashboard's files.

    python tools/smoke_installed.py                 # the ``ingence`` on PATH
    python tools/smoke_installed.py --command /path/to/venv/bin/ingence

Exits non-zero, naming the step, when a step fails. The folder it reads is never written to: the
check compares every file's bytes before and after.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: An invented laboratory's folder: names made up for the check, none a real person's or place's.
FILES = {
    "2026-03-04_zorlin_assay/plate_01_raw.csv": "well,value\nA1,3\nA2,5\n",
    "2026-03-04_zorlin_assay/plate_01_processed.csv": "well,ratio\nA1,0.6\n",
    "2026-03-11_zorlin_assay/plate_02_raw.csv": "well,value\nB1,7\n",
    "notes/readme.txt": "Zorlin assay plates, weekly.\n",
}


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _run(step: str, command: list[str], cwd: Path) -> str:
    print(f"-- {step}: {' '.join(command)}", flush=True)
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        print(done.stdout)
        print(done.stderr, file=sys.stderr)
        message = f"{step} failed with exit code {done.returncode}"
        raise SystemExit(message)
    return done.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--command", default=shutil.which("ingence") or "ingence")
    arguments = parser.parse_args()
    ingence = arguments.command
    with tempfile.TemporaryDirectory(prefix="ingence-smoke-") as made:
        base = Path(made)
        pool = base / "pool"
        for name, text in FILES.items():
            path = pool / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        before = _snapshot(pool)
        project = base / "project"
        said = _run("version", [ingence, "--version"], base)
        if not said.strip():
            message = "version printed nothing"
            raise SystemExit(message)
        _run("init", [ingence, "init", str(project), "--root", str(pool)], base)
        _run("index", [ingence, "index", "--project", str(project), "--quiet"], base)
        _run("infer", [ingence, "infer", "--project", str(project)], base)
        _run("fix", [ingence, "fix", "--project", str(project)], base)
        if _snapshot(pool) != before:
            message = "the data folder changed: Ingence must never write to what it reads"
            raise SystemExit(message)
        if not (project / "CURRENT").is_file():
            message = "no run was published (the project has no CURRENT pointer)"
            raise SystemExit(message)
    print("-- the installed Ingence works", flush=True)


if __name__ == "__main__":
    main()
