"""A restore stopped at any step is finished later, to the same project a whole restore leaves.

A restore writes four things: the Dashboard's settings file, a project Rulebook revision, one
``withdraw`` event and the ``CURRENT`` pointer. Stopped between two of them, it used to leave the
decisions withdrawn while the old run stayed current. The tests stop it at each step, before the
step's write and just after it, and hold that:

- what it set out to do is recorded, so the project knows a restore is unfinished;
- finishing it leaves exactly what an uninterrupted restore leaves (settings, rules, decisions and
  the pointer, compared together), and finishing it again changes nothing, so no withdrawal or
  revision is ever written twice;
- a commit finishes it first, and a run published since the interruption is never replaced.

Every name is invented.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event, in_force, read_events
from ingence.scan.census import run_census
from ingence.storage.commit import read_pointer
from ingence.workspace import restore as restoring
from ingence.workspace import revisions
from ingence.workspace.checkpoints import commit
from ingence.workspace.drafts import save_draft
from ingence.workspace.restore import (
    RestoreInterruptedError,
    finish_interrupted_restore,
    interrupted_restore,
    restore,
    restore_plan,
)

if TYPE_CHECKING:
    from pathlib import Path

R0 = "run_20260101T000001Z_111111111111"
R1 = "run_20260101T000002Z_222222222222"
R2 = "run_20260101T000004Z_444444444444"
R3 = "run_20260101T000008Z_888888888888"
BOUNDED = "candidate_generation_is_bounded"
WHEN = "2026-01-01T00:00:06Z"

#: Each write a restore makes, by the name the module calls it under.
STEPS = {
    "settings": (restoring, "write_dashboard_settings"),
    "revision": (revisions, "write_revision"),
    "withdrawal": (restoring, "append_event"),
    "pointer": (restoring, "write_pointer"),
}


def _build(base: Path) -> Path:
    """R1, then a commit of a setting and a rule parameter, a decision, and R2 current."""
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n", encoding="utf-8")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    save_draft(made.path, "settings", {"scan": {"workers": 5}})
    save_draft(made.path, "rule_parameters", {BOUNDED: {"maximum_candidates": 7}})
    commit(made.path, actor="Ilse Varga", message="tuned", now="2026-01-01T00:00:03Z")
    append_event(
        made.path,
        kind=RecordEventKind.DEFINE,
        actor="Ilse Varga",
        occurred_at="2026-01-01T00:00:03Z",
        target="term:zib",
        against_run_id=R1,
        payload={"answer": "zib_reagent", "theme_kind": "reagent", "raised": "zib"},
    )
    run_inference(made, run_id=R2, started_at="2026-01-01T00:00:04Z")
    return made.path


def _state(project: Path) -> dict[str, Any]:
    """Settings, rules, decisions and the pointer, together."""
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE

    events = read_events(project, held=False, withdrawn=True, moved=False)
    dashboard = project / DASHBOARD_SETTINGS_FILE
    pointer = read_pointer(project)
    return {
        "pointer": pointer.run_id if pointer else None,
        "settings": dashboard.read_text("utf-8") if dashboard.is_file() else None,
        "revisions": {
            path.name: path.read_text("utf-8")
            for path in sorted((project / "rulebook").glob("project-*.yaml"))
        },
        "in_force": sorted(event.event_id for event in in_force(events)),
        "withdrawals": sorted(
            event.event_id for event in events if event.kind is RecordEventKind.WITHDRAW
        ),
    }


@pytest.fixture(scope="module")
def whole(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """What an uninterrupted restore to R1 leaves."""
    project = _build(tmp_path_factory.mktemp("whole"))
    plan = restore_plan(project, R1)
    # The restore touches all four: otherwise a step below would stop nothing.
    assert plan.settings and plan.rules_reverted and plan.withdraw and plan.current_run_id == R2
    restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    assert interrupted_restore(project) is None
    return _state(project)


def _stop(monkeypatch: pytest.MonkeyPatch, step: str, when: str) -> None:
    """Stop the restore at one step: before its write, or just after the write has landed."""
    module, name = STEPS[step]
    real = getattr(module, name)

    def stopped(*args: Any, **kwargs: Any) -> Any:
        if when == "after":
            real(*args, **kwargs)
        raise OSError("the power went off")

    monkeypatch.setattr(module, name, stopped)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", list(STEPS))
def test_a_restore_stopped_at_any_step_is_finished_to_the_same_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    whole: dict[str, Any],
    step: str,
    when: str,
) -> None:
    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    _stop(monkeypatch, step, when)
    with pytest.raises(RestoreInterruptedError, match="stopped before it finished"):
        restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    monkeypatch.undo()
    begun = interrupted_restore(project)
    assert begun is not None and begun["run_id"] == R1

    done = finish_interrupted_restore(project)
    assert done is not None and done["restored_to"] == R1
    assert interrupted_restore(project) is None
    assert _state(project) == whole
    # Finishing again finds nothing to do, and writes nothing twice.
    assert finish_interrupted_restore(project) is None
    assert _state(project) == whole


def test_finishing_a_restore_twice_over_never_doubles_a_withdrawal_or_a_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, whole: dict[str, Any]
) -> None:
    """A finish that is itself stopped, after the withdrawal and revision landed, is finished by
    the next one without writing either again."""
    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    _stop(monkeypatch, "pointer", "before")
    with pytest.raises(RestoreInterruptedError):
        restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    with pytest.raises(RestoreInterruptedError, match="could not be finished"):
        finish_interrupted_restore(project)
    monkeypatch.undo()
    finish_interrupted_restore(project)
    assert _state(project) == whole
    assert len(_state(project)["withdrawals"]) == 1


def test_a_commit_finishes_an_interrupted_restore_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, whole: dict[str, Any]
) -> None:
    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    _stop(monkeypatch, "withdrawal", "before")
    with pytest.raises(RestoreInterruptedError):
        restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    monkeypatch.undo()
    save_draft(project, "settings", {"scan": {"workers": 3}})
    commit(project, actor="Ilse Varga", message="after", now="2026-01-01T00:00:07Z")
    assert interrupted_restore(project) is None
    finished = _state(project)
    assert finished["pointer"] == whole["pointer"]
    assert finished["in_force"] == whole["in_force"]
    assert finished["withdrawals"] == whole["withdrawals"]


def test_a_run_published_after_the_interruption_is_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    _stop(monkeypatch, "pointer", "before")
    with pytest.raises(RestoreInterruptedError):
        restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    monkeypatch.undo()
    run_inference(Project.open(project), run_id=R3, started_at="2026-01-01T00:00:08Z")
    finish_interrupted_restore(project)
    pointer = read_pointer(project)
    assert pointer is not None and pointer.run_id == R3
    assert interrupted_restore(project) is None


def test_a_restore_that_cannot_be_carried_out_writes_nothing_and_records_nothing(
    tmp_path: Path,
) -> None:
    """Every check is made before the intent is recorded: a refused restore leaves no intent to
    finish and changes nothing."""
    project = _build(tmp_path)
    before = _state(project)
    with pytest.raises(restoring.RestoreRefusedError, match="preview"):
        restore(project, run_id=R1, actor="Ilse Varga", previewed="not-the-preview", now=WHEN)
    assert interrupted_restore(project) is None
    assert _state(project) == before


def test_the_withdrawal_names_the_restore_that_wrote_it(tmp_path: Path) -> None:
    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    done = restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    written = next(
        event
        for event in read_events(project, held=False, withdrawn=True, moved=False)
        if event.event_id == done["withdrawal"]
    )
    detail = json.loads(written.payload)
    assert detail["restore_intent"].startswith("restore_")
    assert detail["previewed"] == plan.digest


def test_a_commit_made_while_a_restore_runs_waits_for_it_and_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, whole: dict[str, Any]
) -> None:
    """A running restore holds the drafts' lock for the whole of its writing, so its record is
    never taken for an interrupted one: the commit waits, and both happen."""
    import threading
    import time

    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    real = restoring.append_event
    started = threading.Event()

    def slow(*args: Any, **kwargs: Any) -> Any:
        started.set()
        time.sleep(1.0)
        return real(*args, **kwargs)

    monkeypatch.setattr(restoring, "append_event", slow)
    done: dict[str, Any] = {}

    def go_back() -> None:
        done["restore"] = restore(
            project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN
        )

    worker = threading.Thread(target=go_back)
    worker.start()
    assert started.wait(30)
    save_draft(project, "settings", {"scan": {"workers": 3}})
    made = commit(project, actor="Ilse Varga", message="during", now="2026-01-01T00:00:07Z")
    worker.join(30)
    assert done["restore"]["restored_to"] == R1
    assert made.run_id == R1
    assert interrupted_restore(project) is None
    assert _state(project)["withdrawals"] == whole["withdrawals"]


def test_a_step_the_project_now_refuses_is_left_out_and_named_and_the_rest_is_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, whole: dict[str, Any]
) -> None:
    """A restore stopped, and then a setting it meant to write is refused (an upgrade renamed it):
    finishing it never sticks on that step for ever."""
    project = _build(tmp_path)
    plan = restore_plan(project, R1)
    _stop(monkeypatch, "settings", "before")
    with pytest.raises(RestoreInterruptedError):
        restore(project, run_id=R1, actor="Ilse Varga", previewed=plan.digest, now=WHEN)
    monkeypatch.undo()
    begun = interrupted_restore(project)
    assert begun is not None
    begun["settings"] = {"scan": {"no_such_setting": 1}}
    restoring._write_intent(project, begun)
    done = finish_interrupted_restore(project)
    assert done is not None and done["not_returned"] == ["settings"]
    finished = _state(project)
    assert finished["pointer"] == whole["pointer"]
    assert finished["withdrawals"] == whole["withdrawals"]
    assert finished["revisions"] == whole["revisions"]
