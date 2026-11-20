"""A checkpoint and a project Rulebook revision are written inside the project, whole, or not
at all.

Both are append-only files named by their number. Two failures were possible before:

- A folder on the way to one (``workspace/checkpoints``, ``rulebook``) swapped for a link put the
  file in whatever the link led to, a folder of source data included. The project was checked when
  it was opened, and the write followed the link it met later.
- The final name was created first and written into after, so a write stopped halfway left half a
  file under the name for good. Half a revision stops the Rulebook loading, and with it the
  project.

The tests hold that every such write refuses a link met at the moment of writing, leaves the
outside folder byte for byte as it was, and that a write stopped at any point leaves no file under
the final name.
"""

from __future__ import annotations

import errno
import os
import shutil
import time
from typing import TYPE_CHECKING, Any

import pytest

from ingence.core import destinations
from ingence.core.destinations import publish_new, remove_abandoned
from ingence.core.project import Project
from ingence.rulebook import project_rulebook
from ingence.storage.layout import ProjectLayoutError
from ingence.workspace.checkpoints import commit, list_checkpoints
from ingence.workspace.drafts import save_draft
from ingence.workspace.revisions import write_revision

if TYPE_CHECKING:
    from pathlib import Path

#: One of the shipped Rulebook's own parameters, a whole number.
BOUNDED = "candidate_generation_is_bounded"


@pytest.fixture
def pool(tmp_path: Path) -> Path:
    folder = tmp_path / "pool"
    folder.mkdir()
    (folder / "plate_07.csv").write_text("well,value\nA1,3\n", encoding="utf-8")
    return folder


@pytest.fixture
def project(tmp_path: Path, pool: Path) -> Path:
    return Project.create(tmp_path / "project", created_at="t", roots=[pool]).path


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(folder.rglob("*"))
    }


def _link(link: Path, target: Path) -> None:
    """Replace ``link`` (whatever is there) with a link to the folder ``target``."""
    if link.is_dir() and not link.is_symlink():
        shutil.rmtree(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")


def _incoming(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir() if path.name.endswith(".incoming"))


# -- A link met at the moment of writing is refused --------------------------------------------


def test_a_checkpoints_folder_swapped_for_a_link_after_opening_is_refused(
    project: Path, pool: Path
) -> None:
    opened = Project.open(project).path
    save_draft(opened, "settings", {"scan": {"workers": 5}})
    save_draft(opened, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    before = _snapshot(pool)
    _link(opened / "workspace" / "checkpoints", pool)
    with pytest.raises(ProjectLayoutError, match="workspace/checkpoints"):
        commit(opened, actor="Sam Okafor", message="workers")
    assert _snapshot(pool) == before
    # Refused before the commit wrote anything: neither the settings nor a revision.
    assert not (opened / "settings.dashboard.yaml").exists()
    assert not list((opened / "rulebook").glob("project-*.yaml"))


def test_a_commit_tried_again_after_stopping_at_its_checkpoint_appends_its_revision_once(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    real = destinations._give_name

    def checkpoint_refused(incoming: Path, path: Path) -> None:
        # Only the checkpoint stops: the revision before it is written.
        if path.parent.name == "checkpoints":
            raise OSError(errno.EIO, "the disk went away")
        real(incoming, path)

    monkeypatch.setattr(destinations, "_give_name", checkpoint_refused)
    with pytest.raises(OSError, match="disk"):
        commit(project, actor="Sam Okafor", message="fewer")
    monkeypatch.undo()
    assert [path.name for path in (project / "rulebook").glob("project-*.yaml")] == [
        "project-0001.yaml"
    ]
    made = commit(project, actor="Sam Okafor", message="fewer")
    assert made.project_revision == 1
    assert [path.name for path in (project / "rulebook").glob("project-*.yaml")] == [
        "project-0001.yaml"
    ]
    # A later commit of the same values is a commit of its own, with a revision of its own.
    save_draft(project, "rule_parameters", {BOUNDED: {"maximum_candidates": 30}})
    assert commit(project, actor="Sam Okafor", message="again").project_revision == 2


def test_a_rulebook_folder_swapped_for_a_link_after_opening_is_refused(
    project: Path, pool: Path
) -> None:
    opened = Project.open(project).path
    before = _snapshot(pool)
    _link(opened / "rulebook", pool)
    with pytest.raises(ProjectLayoutError, match="rulebook"):
        write_revision(opened, {BOUNDED: {"maximum_candidates": 30}}, summary="one")
    assert _snapshot(pool) == before


def test_a_link_standing_at_the_final_name_is_never_written_through(
    tmp_path: Path, pool: Path
) -> None:
    before = _snapshot(pool)
    path = tmp_path / "project-0001.yaml"
    try:
        path.symlink_to(pool / "plate_07.csv")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    with pytest.raises(FileExistsError):
        publish_new(path, "project_revision: 1\n")
    assert _snapshot(pool) == before
    assert path.is_symlink()


# -- A stopped write leaves no file under the final name -----------------------------------------


class _HalfWritten:
    """A file handle that writes half of what it is given, then fails, as a full disk does."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def write(self, data: str) -> int:
        self._handle.write(data[: len(data) // 2])
        self._handle.flush()
        raise OSError(errno.ENOSPC, "the disk is full")

    def __enter__(self) -> _HalfWritten:
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle.close()


def _fail_publishing(monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    """Stop the next publish halfway through its bytes, or after they are whole and flushed and
    before they have their name."""
    if where == "write":
        real = destinations.create_exclusive

        def half(path: Path, **options: Any) -> tuple[Any, Path]:
            handle, incoming = real(path, **options)
            # Only a checkpoint's or a revision's own file: the settings a commit lays in first are
            # written by the same helper and must not be the write that stops.
            if path.parent.name in {"checkpoints", "rulebook"}:
                return _HalfWritten(handle), incoming
            return handle, incoming

        monkeypatch.setattr(destinations, "create_exclusive", half)
        return

    def refused(incoming: Path, path: Path) -> None:
        raise OSError(errno.EIO, "the disk went away")

    monkeypatch.setattr(destinations, "_give_name", refused)


STOPS = ("write", "publish")


@pytest.mark.parametrize("where", STOPS)
def test_a_revision_stopped_mid_write_leaves_no_revision_and_the_project_opens(
    project: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    _fail_publishing(monkeypatch, where)
    with pytest.raises(OSError, match="disk"):
        write_revision(project, {BOUNDED: {"maximum_candidates": 30}}, summary="one")
    monkeypatch.undo()
    folder = project / "rulebook"
    assert not (folder / "project-0001.yaml").exists()
    assert _incoming(folder) == []
    assert project_rulebook(project).project_revision == 0
    # The number is free, and the next write takes it.
    assert write_revision(project, {BOUNDED: {"maximum_candidates": 30}}, summary="one") == 1
    assert project_rulebook(project).project_revision == 1


@pytest.mark.parametrize("where", STOPS)
def test_a_checkpoint_stopped_mid_write_leaves_no_checkpoint_and_keeps_the_drafts(
    project: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    save_draft(project, "settings", {"scan": {"workers": 5}})
    # Only the checkpoint's own publish is stopped, never the settings the commit lays in first.
    _fail_publishing(monkeypatch, where)
    with pytest.raises(OSError, match="disk"):
        commit(project, actor="Sam Okafor", message="workers")
    monkeypatch.undo()
    folder = project / "workspace" / "checkpoints"
    assert folder.is_dir()
    assert list(folder.glob("*.json")) == []
    assert _incoming(folder) == []
    assert list_checkpoints(project) == []
    made = commit(project, actor="Sam Okafor", message="workers")
    assert made.sequence == 1
    assert made.changed_settings == ("scan.workers",)
    assert [checkpoint.sequence for checkpoint in list_checkpoints(project)] == [1]


# -- The name is given whole, and never over a file that has it ---------------------------------


def test_publishing_never_replaces_a_file_that_has_the_name(tmp_path: Path) -> None:
    path = tmp_path / "000001.json"
    path.write_text("first", encoding="utf-8")
    with pytest.raises(FileExistsError):
        publish_new(path, "second")
    assert path.read_text(encoding="utf-8") == "first"
    assert _incoming(tmp_path) == []


@pytest.mark.parametrize("code", [errno.EPERM, errno.EXDEV])
def test_a_filesystem_with_no_hard_links_still_publishes_and_still_refuses_a_taken_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    if os.name == "nt":
        pytest.skip("Windows publishes with a rename that refuses a taken name")

    def no_links(source: object, target: object) -> None:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(destinations.os, "link", no_links)
    path = tmp_path / "project-0001.yaml"
    publish_new(path, "project_revision: 1\n")
    assert path.read_text(encoding="utf-8") == "project_revision: 1\n"
    with pytest.raises(FileExistsError):
        publish_new(path, "project_revision: 1\nsummary: other\n")
    assert path.read_text(encoding="utf-8") == "project_revision: 1\n"
    assert _incoming(tmp_path) == []


def test_two_writers_racing_for_one_number_never_overwrite_each_other(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "000001.json"

    def write(text: str) -> str:
        try:
            publish_new(path, text)
        except FileExistsError:
            return "refused"
        return text

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(write, [f"writer {number}" for number in range(8)]))
    won = [result for result in results if result != "refused"]
    assert len(won) == 1
    assert path.read_text(encoding="utf-8") == won[0]


# -- What a dead writer left is cleared, and nothing a live one is writing -----------------------


def test_only_old_temporary_files_of_the_kind_are_removed(tmp_path: Path) -> None:
    old = tmp_path / ".project-0003.yaml.4242.0123abcd.incoming"
    fresh = tmp_path / ".project-0004.yaml.4243.89abcdef.incoming"
    other = tmp_path / ".settings.yaml.4244.00000000.incoming"
    person = tmp_path / "project-0003.yaml"
    for path in (old, fresh, other, person):
        path.write_text("x", encoding="utf-8")
    then = time.time() - 2 * destinations.ABANDONED_SECONDS
    for path in (old, other, person):
        os.utime(path, (then, then))
    removed = remove_abandoned(tmp_path, "project-")
    assert removed == [old]
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        path.name for path in (fresh, other, person)
    )
