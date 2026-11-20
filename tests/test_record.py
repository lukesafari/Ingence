"""The Record's write path: append-only, attributable, and whole or absent.

The Record is one of the four inputs from which every conclusion can be made again, so these tests
are not about a function returning a value. They are about four properties that are cheap to
assert now and impossible to recover once something has been lost:

Nothing is ever rewritten. A second append leaves the first fragment byte for byte as it was.

Every event says who, when, against which run, and what it decides. Each of the four has a refusal
beside it, because a decision missing one of them looks exactly like a decision that can be
replayed and cannot be.

Two appenders never claim one sequence. This is tested with real threads rather than argued about,
because the alternative is two decisions under one identifier in a table whose primary key is that
identifier.

An interrupted append leaves nothing. It does not leave a truncated Parquet file in the one table
that has no second copy anywhere.
"""

from __future__ import annotations

import errno
import os
import threading
from typing import TYPE_CHECKING, Any

import pytest

from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import RecordEvent, RecordEventKind
from ingence.jobs.locks import FileLock, held_elsewhere
from ingence.record import log
from ingence.record.log import (
    LOCK_FILE,
    WRITER_LOCK,
    AppendError,
    RecordLockedError,
    append_event,
    exclusive_creation_refuses,
    fragment_name,
    latest_by_target,
    next_sequence,
    read_events,
    record_directory,
    sequences_present,
)
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

RUN = "run_20260101T000001Z_111111111111"
MOMENT = "2026-09-17T14:02:11Z"


def censused(base: Path) -> Project:
    """A project with one published run, which is what an event is made against."""
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUN, started_at="t")
    return project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return censused(tmp_path)


def confirm(project: Project, target: str, **overrides: object) -> RecordEvent:
    """Append an ordinary confirmation, which is the event the Review view writes."""
    arguments: dict[str, object] = {
        "kind": RecordEventKind.CONFIRM,
        "actor": "robin",
        "occurred_at": MOMENT,
        "target": target,
        "against_run_id": RUN,
    }
    arguments.update(overrides)
    return append_event(project.path, **arguments)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# What an append writes.
# ---------------------------------------------------------------------------


def test_an_event_lands_in_one_fragment_named_for_its_sequence(project: Project) -> None:
    """The name is the sequence, which is what makes a listing the log's index.

    :func:`next_sequence` reads the names rather than the events, so a name that disagreed
    with the row inside it would make every later sequence wrong while looking right.
    """
    event = confirm(project, "entry_abc")
    assert event.sequence == 1
    fragments = sorted(record_directory(project.path).glob("*.parquet"))
    assert [path.name for path in fragments] == [fragment_name(1)]
    assert read_events(project.path)[0].sequence == 1


def test_every_fragment_name_agrees_with_the_sequence_inside_it(project: Project) -> None:
    for index in range(1, 4):
        confirm(project, f"entry_{index}")
    events = {event.sequence: event for event in read_events(project.path)}
    for path in sorted(record_directory(project.path).glob("*.parquet")):
        sequence = int(path.stem)
        assert sequence in events, f"{path.name} names a sequence no event carries"


def test_sequences_start_at_one_and_never_repeat(project: Project) -> None:
    made = [confirm(project, f"entry_{index}") for index in range(5)]
    assert [event.sequence for event in made] == [1, 2, 3, 4, 5]
    assert sequences_present(project.path) == (1, 2, 3, 4, 5)
    assert len({event.event_id for event in made}) == 5


def test_reading_returns_events_in_the_order_they_were_made(project: Project) -> None:
    confirm(project, "second_thing")
    confirm(project, "first_thing")
    events = read_events(project.path)
    assert [event.target for event in events] == ["second_thing", "first_thing"]
    assert [event.sequence for event in events] == [1, 2]


def test_an_event_carries_who_when_against_which_run_and_what_it_decides(
    project: Project,
) -> None:
    event = confirm(
        project,
        "edge_xyz",
        kind=RecordEventKind.REJECT,
        payload={"reason": "different experiment", "certainty": "sure"},
    )
    assert event.actor == "robin"
    assert event.occurred_at == MOMENT
    assert event.against_run_id == RUN
    assert event.kind is RecordEventKind.REJECT
    assert event.target == "edge_xyz"
    assert event.payload == '{"certainty":"sure","reason":"different experiment"}'


def test_a_payload_is_canonical_however_it_was_written(project: Project) -> None:
    """Two spellings of one decision are one decision.

    A payload serialised with unordered keys differs between two writers describing the
    same thing, which is one of the two mechanical causes of nondeterminism this project
    closes everywhere else.
    """
    first = confirm(project, "a", payload={"b": 2, "a": 1})
    second = confirm(project, "b", payload={"a": 1, "b": 2})
    assert first.payload == second.payload


def test_a_scope_is_recorded_and_defaults_to_the_project(project: Project) -> None:
    """The most common real correction is not "this is wrong" but "this is wrong here"."""
    wide = confirm(project, "rule_stage_from_name")
    narrow = confirm(project, "rule_stage_from_name", scope=Scope(ScopeKind.FOLDER, "kelt/raw"))
    assert wide.scope == PROJECT_SCOPE
    assert narrow.scope.kind is ScopeKind.FOLDER
    assert read_events(project.path)[1].scope == Scope(ScopeKind.FOLDER, "kelt/raw")


# ---------------------------------------------------------------------------
# Append-only, and it must be by construction.
# ---------------------------------------------------------------------------


def test_a_second_append_leaves_the_first_fragment_byte_for_byte(project: Project) -> None:
    confirm(project, "first")
    first = record_directory(project.path) / fragment_name(1)
    before = first.read_bytes()
    confirm(project, "second")
    assert first.read_bytes() == before


def test_a_later_decision_wins_and_the_earlier_one_stays_readable(project: Project) -> None:
    """Changing your mind is a decision, not a correction to the log.

    ``latest_by_target`` is what "what does the Record currently say" means, and every
    superseded decision is still there to be read, which is the whole reason the log is
    append-only rather than a table of current answers.
    """
    confirm(project, "class_7", kind=RecordEventKind.CONFIRM)
    confirm(project, "class_7", kind=RecordEventKind.REJECT)
    events = read_events(project.path)
    assert len(events) == 2
    assert latest_by_target(events)["class_7"].kind is RecordEventKind.REJECT
    assert events[0].kind is RecordEventKind.CONFIRM


def test_nothing_can_ask_for_an_event_to_be_born_orphaned() -> None:
    """Orphanhood is what a reader concludes, never what a writer asserts.

    An append-only log cannot flip a flag on a row it has written, so the ceiling is held
    by there being no argument rather than by a check somebody could remove.
    """
    import inspect

    assert "orphaned" not in inspect.signature(append_event).parameters


# ---------------------------------------------------------------------------
# The four refusals, one per thing an event must carry.
# ---------------------------------------------------------------------------


def test_an_event_naming_no_actor_is_refused(project: Project) -> None:
    with pytest.raises(AppendError, match="names no actor"):
        confirm(project, "entry_abc", actor="   ")
    assert read_events(project.path) == ()


def test_an_event_naming_no_target_is_refused(project: Project) -> None:
    with pytest.raises(AppendError, match="names no target"):
        confirm(project, "")


def test_an_event_with_no_moment_is_refused(project: Project) -> None:
    with pytest.raises(AppendError, match="states no moment"):
        confirm(project, "entry_abc", occurred_at="  ")


def test_a_moment_without_a_zone_is_refused(project: Project) -> None:
    """A wall time with no zone is hours wrong somewhere and looks right."""
    with pytest.raises(AppendError, match="states no zone"):
        confirm(project, "entry_abc", occurred_at="2026-09-17T14:02:11")


def test_a_moment_that_is_not_an_instant_is_refused(project: Project) -> None:
    with pytest.raises(AppendError, match="not an instant"):
        confirm(project, "entry_abc", occurred_at="last tuesday")


def test_an_event_against_a_run_the_project_never_published_is_refused(
    project: Project,
) -> None:
    """A decision is read in the light of what the person was looking at.

    A run identifier naming nothing makes that impossible while looking exactly like a
    decision that can be read, which is the failure worth refusing.
    """
    with pytest.raises(AppendError, match="no run"):
        confirm(project, "entry_abc", against_run_id="run_20260101T000009Z_999999999999")
    assert read_events(project.path) == ()


# ---------------------------------------------------------------------------
# Two appenders.
# ---------------------------------------------------------------------------


def test_exclusive_creation_is_measured_on_this_platform(tmp_path: Path) -> None:
    """The lock rests on a platform property, so the property is measured.

    Every platform assumption this project made without measuring turned out to be wrong
    somewhere, and this is the one the whole write path depends on.
    """
    assert exclusive_creation_refuses(tmp_path) is True


def test_concurrent_appends_each_take_their_own_sequence(project: Project) -> None:
    """The reason the lock exists, asserted rather than argued.

    Reading the last sequence and then writing it is not atomic, so two appenders that
    both read 7 would both write 7: two decisions under one identifier, in a table whose
    primary key is that identifier.
    """
    count = 8
    barrier = threading.Barrier(count)
    made: list[RecordEvent] = []
    failures: list[BaseException] = []
    guard = threading.Lock()

    def append(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            event = confirm(project, f"entry_{index}")
        except BaseException as exc:
            with guard:
                failures.append(exc)
        else:
            with guard:
                made.append(event)

    threads = [
        threading.Thread(target=append, args=(index,), daemon=True) for index in range(count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, f"{len(failures)} appends failed: {failures[0]!r}"
    assert sorted(event.sequence for event in made) == list(range(1, count + 1))
    assert sequences_present(project.path) == tuple(range(1, count + 1))
    assert len({event.event_id for event in read_events(project.path)}) == count


def test_a_lock_a_live_holder_keeps_is_named_rather_than_waited_on(project: Project) -> None:
    """A holder that never finishes is stuck, and saying so is the useful act.

    The message names the lock, and says it is let go of when the holder finishes or exits.
    """
    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    holder = FileLock(directory / WRITER_LOCK)
    assert holder.acquire()
    try:
        with pytest.raises(RecordLockedError, match="was held for more than") as refused:
            confirm(project, "entry_abc", timeout=0.1)
        assert "finishes or exits" in str(refused.value)
    finally:
        holder.release()
    assert read_events(project.path) == ()
    assert confirm(project, "entry_abc").sequence == 1


def test_the_lock_is_released_even_when_an_append_fails(project: Project, monkeypatch) -> None:
    """A refusal must not leave the Record unwritable for the rest of the session."""
    monkeypatch.setattr(log, "write_fragment", _explode)
    with pytest.raises(RuntimeError, match="disk went away"):
        confirm(project, "entry_abc")
    directory = record_directory(project.path)
    assert not (directory / LOCK_FILE).exists()
    assert not held_elsewhere(directory / WRITER_LOCK)
    monkeypatch.undo()
    assert confirm(project, "entry_abc").sequence == 1


_HOLD_AND_DIE = """
import os, sys, time
from pathlib import Path
from ingence.record import log

directory = Path(sys.argv[1])
with log._held(directory, timeout=5):
    print("held", flush=True)
    time.sleep(60)
"""


def test_an_appender_killed_holding_the_lock_never_blocks_the_next(project: Project) -> None:
    """The lock is the operating system's, so a holder that dies lets go of it as it dies. The
    file older engines wait on, which it marked and left, is removed by the next holder at once."""
    import subprocess
    import sys
    import time

    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_AND_DIE, str(directory)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        assert (directory / LOCK_FILE).exists()
        assert held_elsewhere(directory / WRITER_LOCK)
    finally:
        child.kill()
        child.wait(timeout=30)
        if child.stdout is not None:
            child.stdout.close()
    # Left behind, as a killed holder leaves it.
    assert (directory / LOCK_FILE).exists()
    started = time.monotonic()
    assert confirm(project, "entry_abc", timeout=5).sequence == 1
    assert time.monotonic() - started < 2.0
    assert not (directory / LOCK_FILE).exists()


def test_an_older_engines_lock_is_waited_on_while_it_holds_it(project: Project) -> None:
    """An older engine's lock (a file created exclusively, unmarked) is a holder at work while it
    changes: the append waits for it and goes on when it is let go of."""
    import time

    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)

    def let_go() -> None:
        time.sleep(0.3)
        (directory / LOCK_FILE).unlink()

    releaser = threading.Thread(target=let_go)
    releaser.start()
    started = time.monotonic()
    try:
        assert confirm(project, "entry_abc", timeout=5).sequence == 1
    finally:
        releaser.join()
    waited = time.monotonic() - started
    assert 0.25 <= waited < 4.0


def test_an_older_engines_lock_that_outlasts_the_wait_is_named_and_kept(project: Project) -> None:
    """An older engine's lock says nothing of its holder, so how long it has stood proves nothing:
    a slow holder on a slow share is alive. It is named, as before, and never removed."""
    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with pytest.raises(RecordLockedError, match="remove the file"):
        confirm(project, "entry_abc", timeout=0.2)
    assert (directory / LOCK_FILE).exists()
    assert read_events(project.path) == ()


def _marked(directory: Path, *, host: str, pid: int) -> None:
    import json

    (directory / LOCK_FILE).write_bytes(
        log._LOCK_MARK + json.dumps({"host": host, "pid": pid, "token": "0" * 16}).encode() + b"\n"
    )


def test_a_lock_naming_another_computer_is_never_taken_for_a_dead_holders(
    project: Project,
) -> None:
    """On a shared drive, a holder on another computer cannot be looked at from this one."""
    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    _marked(directory, host="another-computer.invalid", pid=999_999)
    with pytest.raises(RecordLockedError):
        confirm(project, "entry_abc", timeout=0.2)
    assert (directory / LOCK_FILE).exists()


def test_a_lock_naming_a_live_process_here_is_waited_on(project: Project) -> None:
    import socket

    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    # The test's parent is alive for the whole test.
    _marked(directory, host=socket.gethostname(), pid=os.getppid())
    with pytest.raises(RecordLockedError):
        confirm(project, "entry_abc", timeout=0.2)
    assert (directory / LOCK_FILE).exists()


@pytest.mark.parametrize("name", [WRITER_LOCK, LOCK_FILE])
def test_a_link_at_either_lock_is_refused_and_nothing_is_made_where_it_leads(
    project: Project, tmp_path: Path, name: str
) -> None:
    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (directory / name).symlink_to(elsewhere / "planted.lock")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    with pytest.raises(log.AppendError, match="link"):
        confirm(project, "entry_abc")
    assert list(elsewhere.iterdir()) == []
    assert read_events(project.path) == ()


def test_an_older_engine_waits_while_this_one_holds_the_record(project: Project) -> None:
    """While this engine holds the Record, the file an older engine creates exclusively is there,
    so the older engine's own claim waits."""
    directory = record_directory(project.path)
    directory.mkdir(parents=True, exist_ok=True)
    with log._held(directory, timeout=1), pytest.raises(FileExistsError):
        os.open(directory / LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    assert not (directory / LOCK_FILE).exists()


def _explode(*args: object, **kwargs: object) -> None:
    message = "disk went away"
    raise RuntimeError(message)


def test_an_interrupted_append_leaves_no_fragment_in_the_record(
    project: Project, monkeypatch
) -> None:
    """Whole or absent. A truncated Parquet file here would make every later read fail.

    The fragment is written into the staging directory and flushed before it is moved in,
    so a failure between the two leaves the Record exactly as it was.
    """
    monkeypatch.setattr(log, "fsync_file", _explode)
    with pytest.raises(RuntimeError, match="disk went away"):
        confirm(project, "entry_abc")
    assert sequences_present(project.path) == ()
    assert read_events(project.path) == ()


def test_a_file_in_the_record_that_is_not_named_for_a_sequence_is_refused(
    project: Project,
) -> None:
    """A fragment nobody can place in the order is not something to guess about."""
    confirm(project, "entry_abc")
    (record_directory(project.path) / "yesterday.parquet").write_bytes(b"")
    with pytest.raises(AppendError, match="not named for a sequence"):
        next_sequence(project.path)


# ---------------------------------------------------------------------------
# What the rest of the engine can see.
# ---------------------------------------------------------------------------


def test_the_record_spans_runs_and_is_queryable_beside_the_index(project: Project) -> None:
    """The Record is an input to a run, not an output of it, so it is read the same way.

    ``record_events`` is already declared ``spans_runs``, which is why the catalog finds it
    under the project rather than under a run directory: a run rewriting its own tables must
    never be able to rewrite a decision.
    """
    confirm(project, "entry_abc", payload={"stage": "acquisition"})
    confirm(project, "entry_def")
    with open_catalog(project.path) as catalog:
        assert catalog.count("record_events") == 2
        rows = catalog.query(
            "SELECT sequence, actor, target, payload FROM record_events ORDER BY sequence"
        ).fetchall()
    assert [row[0] for row in rows] == [1, 2]
    assert {row[1] for row in rows} == {"robin"}
    assert rows[0][3] == '{"stage":"acquisition"}'


def test_an_empty_record_reads_as_no_decisions_rather_than_an_error(project: Project) -> None:
    """A project where nobody has decided anything is the ordinary case, not a broken one."""
    assert read_events(project.path) == ()
    assert next_sequence(project.path) == 1
    with open_catalog(project.path) as catalog:
        assert catalog.count("record_events") == 0


# ---------------------------------------------------------------------------
# A held read: one open per fragment per process, checked every read.
# ---------------------------------------------------------------------------


def _counting_opens(monkeypatch) -> list[str]:
    """Count the fragments a read opens, by name."""
    opened: list[str] = []
    real = log.pq.read_table

    def counted(path: object, **kwargs: object) -> object:
        opened.append(os.path.basename(str(path)))
        return real(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(log.pq, "read_table", counted)
    return opened


def test_a_second_read_opens_only_the_fragments_written_since(
    project: Project, monkeypatch
) -> None:
    """A second read opens only the fragments written since the first.

    A process holds what it read and, on the next read, opens only what is new.
    """
    for index in range(3):
        confirm(project, f"entry_{index}")
    log._HELD.forget()
    opened = _counting_opens(monkeypatch)
    first = read_events(project.path)
    assert sorted(opened) == [fragment_name(1), fragment_name(2), fragment_name(3)]
    opened.clear()
    assert read_events(project.path) == first
    assert opened == []
    confirm(project, "entry_new")
    later = read_events(project.path)
    assert opened == [fragment_name(4)]
    assert later == read_events(project.path, held=False)
    assert [event.target for event in later][-1] == "entry_new"


def test_a_held_read_notices_a_fragment_replaced_or_gone(project: Project) -> None:
    """A fragment is never rewritten, so one that changed means the held copy is stale.

    Replacing a fragment (a new file number and modification time) or removing one discards
    what was held, and the next read is the fragments' own, not the memory of them.
    """
    for index in range(3):
        confirm(project, f"entry_{index}")
    held = read_events(project.path)
    directory = record_directory(project.path)
    second = directory / fragment_name(2)
    replacement = directory.parent / "replacement.bin"
    replacement.write_bytes((directory / fragment_name(1)).read_bytes())
    os.replace(replacement, second)
    after = read_events(project.path)
    assert after == read_events(project.path, held=False)
    assert after != held
    (directory / fragment_name(3)).unlink()
    assert read_events(project.path) == read_events(project.path, held=False)
    assert len(read_events(project.path)) == 2


def test_a_held_read_writes_nothing(project: Project) -> None:
    """Holding is memory only: the Record directory and the project are as they were."""
    confirm(project, "entry_abc")

    def listing() -> list[tuple[str, int]]:
        return sorted(
            (str(path.relative_to(project.path)), path.stat().st_size)
            for path in project.path.rglob("*")
            if path.is_file()
        )

    before = listing()
    read_events(project.path)
    read_events(project.path)
    assert listing() == before


def test_a_lock_whose_deletion_is_completing_is_waited_through_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses, for a moment, to create a file whose deletion is still completing, and
    says "permission denied". It happens under concurrent appends."""
    real_open = os.open
    refusals = [PermissionError(errno.EACCES, "Permission denied")] * 2

    def pending_then_free(path: Any, flags: int, mode: int = 0o777) -> int:
        if refusals and str(path).endswith(log.LOCK_FILE):
            raise refusals.pop()
        return real_open(path, flags, mode)

    monkeypatch.setattr(log.os, "open", pending_then_free)
    monkeypatch.setattr(log, "_DELETE_PENDING_REFUSES", True)
    descriptor = log._claim_lock(tmp_path, timeout=5)
    log._release_lock(tmp_path, descriptor)
    assert not refusals


def test_elsewhere_a_refused_lock_means_the_record_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(path: Any, flags: int, mode: int = 0o777) -> int:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(log.os, "open", refused)
    monkeypatch.setattr(log, "_DELETE_PENDING_REFUSES", False)
    with pytest.raises(log.AppendError):
        log._claim_lock(tmp_path, timeout=5)
