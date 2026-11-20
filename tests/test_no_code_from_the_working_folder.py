"""No process the engine starts imports Python from the folder it was started in.

Python puts the current folder first on the path of a ``python -c`` or ``python -m`` child, ahead
of the engine and its libraries. The read workers start as ``python -c`` (multiprocessing's spawn),
and the Dashboard's job workers as ``python -m``. A person often starts Ingence inside a project
that sits in a shared data folder, where anybody who can write a folder named ``ingence``, or a
``multiprocessing`` package, would have it run as that person on the next scan. Ingence never runs
code it finds. The tests plant both in the folder a dive is started from and hold that neither
runs, and that the job worker is started so that it cannot.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PLANTED = """from pathlib import Path
Path({marker!r}).write_text("ran", encoding="utf-8")
raise SystemExit("a planted module ran")
"""


def _plant(folder: Path, package: str, marker: Path) -> None:
    (folder / package).mkdir(parents=True)
    (folder / package / "__init__.py").write_text(
        PLANTED.format(marker=str(marker)), encoding="utf-8"
    )


def test_a_dive_started_in_a_folder_holding_planted_packages_runs_neither(tmp_path: Path) -> None:
    command = Path(sys.executable).with_name("ingence")
    if not command.exists():
        pytest.skip("the ingence command is not installed beside this interpreter")
    pool = tmp_path / "pool"
    pool.mkdir()
    for index in range(3):
        (pool / f"zorlin_{index:02d}.csv").write_text("well,value\nA1,3\n", encoding="utf-8")
    project = tmp_path / "project"
    started_in = tmp_path / "shared"
    started_in.mkdir()
    markers = [tmp_path / "ran-ingence", tmp_path / "ran-multiprocessing"]
    _plant(started_in, "ingence", markers[0])
    _plant(started_in, "multiprocessing", markers[1])
    for arguments in (
        ["init", str(project), "--root", str(pool)],
        ["index", "--project", str(project), "--quiet"],
        ["dive", "--project", str(project)],
    ):
        done = subprocess.run(
            [str(command), *arguments],
            cwd=started_in,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert done.returncode == 0, done.stderr[-2000:]
    assert [marker for marker in markers if marker.exists()] == []


def test_the_job_worker_is_started_with_no_folder_of_its_callers_on_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.core.project import Project
    from ingence.jobs import supervisor

    project = Project.create(tmp_path / "project", created_at="t").path
    seen: dict[str, Any] = {}

    class Started:
        def __init__(self, argv: list[str], **options: Any) -> None:
            seen["argv"], seen["env"] = argv, options.get("env") or {}

    monkeypatch.setattr(supervisor.subprocess, "Popen", Started)
    job = type("Job", (), {"job_id": "job_20260101T000000Z_00000000", "attempts": 1})()
    supervisor.spawn_worker(project, job, heartbeat_seconds=1.0)  # type: ignore[arg-type]
    assert seen["argv"][1:3] == ["-P", "-m"]
    assert seen["env"].get("PYTHONSAFEPATH") == "1"
