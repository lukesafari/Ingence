"""No process the engine starts outlives the process that started it.

A read worker busy inside a file never looks at the pipe that would tell it its parent is gone, so
it watches its parent and ends itself. A worker that kept reading after its parent was killed ran
at full speed, adopted by the system, with the file open. The jobs supervisor stops a job's worker
together with everything the worker started. Every test here starts real processes, kills one
and looks for the others by their process identifiers, and leaves nothing running whatever the
outcome. Every name is invented.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from ingence.core.project import Project
from ingence.jobs import JobState
from ingence.jobs import supervisor as supervisor_module
from ingence.jobs.ledger import JobLedger
from ingence.jobs.supervisor import JobSupervisor

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="a process is looked for here with a null signal, which Windows takes for a kill",
)

#: Every wait here is bounded. A loaded machine spawning interpreters is slow, and a wait that
#: never ends is the failure these tests exist to catch.
PATIENCE = 60.0

#: How long a worker whose parent is gone may take to end. The watch notices at once; this is
#: the margin for a loaded machine.
PROMPTLY = 10.0

#: A reader that says it has the file in hand, then stays busy on it in Python, as a reader
#: working through a large file is. It gives up after two minutes, so a worker that the watch
#: failed to end does not burn a core for ever.
_READER = '''
"""A reader that stays busy on its file, for the lifetime tests."""

import os
import time
from pathlib import Path

from ingence.extractors.base import EXTRACTORS, ExtractorResult, Isolation


class BusyReader:
    """Writes its process identifier beside the test, then works on the file."""

    name = "lifetime_busy"
    version = "1"
    isolation = Isolation.SUBPROCESS

    def supports(self, context):
        return ".lifetime" in context.extensions

    def extract(self, context):
        Path(os.environ["LIFETIME_MARKER"]).write_text(str(os.getpid()), encoding="utf-8")
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            pass
        return ExtractorResult(extractor=self.name, extractor_version=self.version)


if "lifetime_busy" not in EXTRACTORS.names():
    EXTRACTORS.register("lifetime_busy", version="1", summary="Test fixture: busy on its file")(
        BusyReader
    )
'''

#: The parent: a read pool, or the one-file supervisor, reading the one file for ten minutes.
_PARENT = """
import sys
from pathlib import Path

import lifetime_reader  # noqa: F401 - registers the reader here as in the child
from ingence.core.schema import Entry, EntryType
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors import load_builtins

how, data = sys.argv[1], Path(sys.argv[2])
load_builtins()
stat = data.stat()
if how == "pool":
    from ingence.executors.pool import ReadContext, ReadPool, ReadTask

    entry = Entry(
        entry_id="entry_001",
        root_id="root_x",
        relative_path=data.name,
        name=data.name,
        entry_type=EntryType.FILE,
        extension="lifetime",
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )
    context = ReadContext(
        settings=Settings(extraction=ExtractionSettings(enabled=True, adapters=("lifetime_busy",))),
        run_id="run_20260101T000002Z_222222222222",
        observed_at="t",
        extractors=("lifetime_busy",),
        extra_modules=("lifetime_reader",),
    )
    task = ReadTask(sequence=0, entry=entry, path=str(data), deadline_seconds=600.0)
    with ReadPool(context, workers=1, memory_bytes=1 << 30) as pool:
        for _ in pool.read([task]):
            pass
else:
    from ingence.executors.supervisor import Supervisor
    from ingence.extractors.base import ExtractionContext
    from ingence.extractors.limits import Limits

    context = ExtractionContext(
        entry_id="entry_001",
        path=data,
        relative_path=data.name,
        extensions=frozenset({".lifetime"}),
        size_bytes=stat.st_size,
        media_type=None,
        limits=Limits(),
    )
    Supervisor(timeout_seconds=600.0, memory_bytes=1 << 30).run("lifetime_busy", context)
"""


def _running(pid: int) -> bool:
    """Whether a process runs. One that has ended and waits to be reaped is not running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    shown = subprocess.run(
        [shutil.which("ps") or "/bin/ps", "-o", "stat=", "-p", str(int(pid))],
        capture_output=True,
        text=True,
        check=False,
    )
    state = shown.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _until(condition: Any, what: str, *, timeout: float = PATIENCE) -> Any:
    """Poll until ``condition()`` is truthy and return it, or fail saying what never happened."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        time.sleep(0.05)
    pytest.fail(f"within {timeout} s: {what}")


def _pid_in(marker: Path) -> int | None:
    try:
        said = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(said) if said.isdigit() else None


def _kill_if_running(pid: int | None) -> None:
    if pid is not None and _running(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("how", ["pool", "supervisor"])
def test_a_reader_busy_in_a_file_ends_itself_when_its_parent_is_killed(
    tmp_path: Path, how: str
) -> None:
    """The parent is killed with ``SIGKILL`` while its child is inside the file, with ten minutes
    left on the file's clock. The child is gone within seconds, not adopted and left reading."""
    import ingence

    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "lifetime_reader.py").write_text(textwrap.dedent(_READER), encoding="utf-8")
    data = tmp_path / "pool" / "frames.lifetime"
    data.parent.mkdir()
    data.write_text("frame,value\n1,2\n", encoding="utf-8")
    marker = tmp_path / "reader.pid"
    source = str(Path(ingence.__file__).resolve().parent.parent)
    parent = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(_PARENT), how, str(data)],
        stdin=subprocess.DEVNULL,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([source, str(modules)]),
            "LIFETIME_MARKER": str(marker),
        },
    )
    reader: int | None = None
    try:
        reader = _until(lambda: _pid_in(marker), "the reader had the file in hand")
        assert reader != parent.pid
        assert parent.poll() is None, "the parent is still waiting on its reader"
        parent.kill()
        parent.wait(PATIENCE)
        gone_by = time.monotonic() + PROMPTLY
        while time.monotonic() < gone_by and _running(reader):
            time.sleep(0.05)
        assert not _running(reader), (
            f"the reader outlived its parent by {PROMPTLY:.0f} s, still reading the file"
        )
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(PATIENCE)
        _kill_if_running(reader)


#: A stand-in for a job's worker: it starts a child of its own, as a dive starts read workers,
#: says the child's process identifier, and waits. Both give up after five minutes.
_WORKER = """
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
time.sleep(300)
"""


def test_a_stopped_job_s_worker_is_stopped_with_everything_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person's Stop that the worker does not answer is carried out by the supervisor after the
    phase's grace. The worker's own children go with it.

    The worker is started by ``spawn_worker`` as always, with its platform's options (a session of
    its own on POSIX). Only the program it runs is a stand-in, one that starts a child and waits.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_01.csv").write_text("well,value\nA1,1\n", encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    marker = tmp_path / "child.pid"
    real_popen = subprocess.Popen

    def popen(args: Any, *rest: Any, **options: Any) -> Any:
        if isinstance(args, list) and args[1:3] == ["-m", supervisor_module.WORKER_MODULE]:
            args = [sys.executable, "-c", textwrap.dedent(_WORKER), str(marker)]
        return real_popen(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(supervisor_module, "STOP_GRACE_SECONDS", {})
    monkeypatch.setattr(supervisor_module, "STOP_GRACE_DEFAULT_SECONDS", 0.5)
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn3q",), record_sequence=None, base_run=None)
    supervisor = JobSupervisor(project.path)
    supervisor.start()
    # A job queued before this supervisor waits for a person's Re-run.
    supervisor.release_held()
    child: int | None = None
    worker: int | None = None
    try:
        child = _until(lambda: _pid_in(marker), "the worker started its child")
        started = supervisor.worker_process()
        assert started is not None
        worker = started.pid
        assert ledger.request_stop(job.job_id, keep=False) is not None
        _until(
            lambda: (
                (row := ledger.get(job.job_id)) is not None and row.state is not JobState.RUNNING
            ),
            "the supervisor stopped the job",
        )
        assert not _running(worker)
        gone_by = time.monotonic() + PROMPTLY
        while time.monotonic() < gone_by and _running(child):
            time.sleep(0.05)
        assert not _running(child), "the worker's child outlived the stop"
    finally:
        supervisor.stop()
        _kill_if_running(child)
        _kill_if_running(worker)
    done = ledger.get(job.job_id)
    assert done is not None and done.state is JobState.CANCELLED, done and done.state
