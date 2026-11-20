"""A dive reaches each file from its root and never through a link or a junction.

A census records a folder of the root and the files in it. Suppose the folder is then swapped for a
link to a folder outside the root, and that folder holds a file of the same name, size and
modification time. A dive that built the path as mount plus relative path, and let ``stat`` and
``open`` follow it, would read the outside file and file its text under the entry the census made.
So the dive walks to each file from its root one name at a time and refuses a link or a junction
on the way. It opens only a regular file, and where the settings compare identity
(``extraction.freshness_checks_identity``), only one whose inode and device are the census's. A
refusal is recorded as a file changed since the census is: against the file, never a crash, and
never a read.

Every name is invented.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from ingence.core import platform
from ingence.core.platform import NotTheRecordedFileError, open_beneath, stat_beneath
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors.base import ExtractionContext
from ingence.scan import deepen
from ingence.scan.census import run_census
from ingence.scan.deepen import deepen_entry, run_deepen
from ingence.storage.catalog import open_catalog

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"

#: The file the census saw, and one outside the root of the same length, whose coined word must
#: never reach the Index.
INSIDE = "kelt was here too\n"
PLANTED = "zorbleth was here\n"
MARK = "zorbleth"


def _pool(base: Path) -> tuple[Path, Path]:
    """A root holding ``zib/kelt.txt``, and a folder outside it holding a twin of the same size
    and modification time."""
    assert len(INSIDE) == len(PLANTED)
    pool = base / "pool"
    (pool / "zib").mkdir(parents=True)
    inside = pool / "zib" / "kelt.txt"
    inside.write_text(INSIDE)
    outside = base / "outside"
    outside.mkdir()
    planted = outside / "kelt.txt"
    planted.write_text(PLANTED)
    stamp = inside.stat()
    os.utime(planted, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    return pool, outside


def _swap_for_a_link(pool: Path, outside: Path) -> None:
    """The census's folder taken away and a link to the outside folder put in its place."""
    shutil.rmtree(pool / "zib")
    (pool / "zib").symlink_to(outside, target_is_directory=True)


def _entry(path: Path, relative: str) -> Entry:
    """The entry a census would make of ``path``: its size, time, inode and device."""
    found = path.lstat()
    return Entry(
        entry_id=f"entry_{path.name}",
        root_id="root_x",
        relative_path=relative,
        name=path.name,
        entry_type=EntryType.FILE,
        extension=path.suffix.lstrip("."),
        size_bytes=found.st_size,
        modified_ns=found.st_mtime_ns,
        inode=found.st_ino,
        device=found.st_dev,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


def _settings(**extraction: object) -> Settings:
    return Settings(
        extraction=ExtractionSettings(
            enabled=True,
            use_subprocess_isolation=False,
            **extraction,  # type: ignore[arg-type]
        )
    )


@pytest.mark.parametrize("identity", [True, False])
def test_a_folder_swapped_for_a_link_out_of_the_root_is_refused_and_nothing_outside_is_read(
    tmp_path: Path, identity: bool
) -> None:
    """The audit's case, end to end: index, swap the folder for a link, dive. Whether or not the
    settings compare identity, the walk from the root meets the link and stops there."""
    pool, outside = _pool(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=_settings(freshness_checks_identity=identity),
    )
    run_census(project, run_id=CENSUS, started_at="t")
    _swap_for_a_link(pool, outside)
    report = run_deepen(project, run_id=DIVE, started_at="t", budget_seconds=60)

    with open_catalog(project.path) as catalog:
        said = catalog.query(
            "SELECT coalesce(value_text, '') || coalesce(value_structure, '') FROM observations"
        ).fetchall()
        (outcome,) = catalog.query(
            "SELECT scan_outcome FROM entries WHERE relative_path = 'zib/kelt.txt'"
        ).fetchone()
        errors = catalog.query(
            "SELECT operation, message FROM scan_errors WHERE relative_path = 'zib/kelt.txt'"
        ).fetchall()
    assert not [text for (text,) in said if MARK in text], "the outside file's text was read"
    assert outcome == ScanOutcome.CHANGED_DURING_READ.value
    assert report.changed_during_read == 1
    ((operation, message),) = errors
    assert operation == "freshness"
    assert "passes through a link at zib" in message
    assert "ingence index" in message


def test_a_file_swapped_for_a_link_is_refused_before_it_is_read(tmp_path: Path) -> None:
    pool, outside = _pool(tmp_path)
    path = pool / "zib" / "kelt.txt"
    entry = _entry(path, "zib/kelt.txt")
    path.unlink()
    path.symlink_to(outside / "kelt.txt")

    outcome = deepen_entry(entry, path, run_id="r", observed_at="t", settings=_settings())
    assert outcome.outcome is ScanOutcome.CHANGED_DURING_READ
    assert not outcome.observations
    (error,) = outcome.errors
    assert "it is now a link (zib/kelt.txt)" in error.message


def test_a_folder_swapped_for_a_link_mid_reading_gives_no_reader_a_byte_from_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The swap lands after the dive's first look and before any reader opens the file. Every
    opening walks from the root again, so each reader is refused, and the look after the reading
    says why the file's readings are dropped."""
    pool, outside = _pool(tmp_path)
    path = pool / "zib" / "kelt.txt"
    entry = _entry(path, "zib/kelt.txt")
    planted = (outside / "kelt.txt").stat().st_ino

    looked = deepen.source_state

    def swapped_after_the_first_look(*args: object) -> object:
        state = looked(*args)  # type: ignore[arg-type]
        if (pool / "zib").is_dir() and not (pool / "zib").is_symlink():
            _swap_for_a_link(pool, outside)
        return state

    monkeypatch.setattr(deepen, "source_state", swapped_after_the_first_look)
    opening = ExtractionContext.open
    opened: list[int] = []

    def every_opening(self: ExtractionContext) -> object:
        handle = opening(self)
        opened.append(os.fstat(handle.fileno()).st_ino)
        return handle

    monkeypatch.setattr(ExtractionContext, "open", every_opening)

    outcome = deepen_entry(entry, path, run_id="r", observed_at="t", settings=_settings())
    assert planted not in opened, "a reader opened the file outside the root"
    assert outcome.outcome is ScanOutcome.CHANGED_DURING_READ
    assert not outcome.observations
    (error,) = outcome.errors
    assert "passes through a link at zib" in error.message


@pytest.mark.parametrize("identity", [True, False])
def test_a_file_replaced_under_the_same_size_and_time_is_refused_by_its_inode(
    tmp_path: Path, identity: bool
) -> None:
    """Size and time are what the census compared before. A file replaced by a program that sets
    the time back, or reached through a swapped folder, keeps both; its inode tells it apart."""
    pool, _ = _pool(tmp_path)
    path = pool / "zib" / "kelt.txt"
    entry = _entry(path, "zib/kelt.txt")
    stamp = path.stat()
    replacement = pool / "zib" / "kelt.new"
    replacement.write_text(PLANTED)
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    os.replace(replacement, path)
    assert path.stat().st_ino != entry.inode

    outcome = deepen_entry(
        entry,
        path,
        run_id="r",
        observed_at="t",
        settings=_settings(freshness_checks_identity=identity),
    )
    assert (outcome.outcome is ScanOutcome.CHANGED_DURING_READ) is identity
    if identity:
        (error,) = outcome.errors
        assert f"inode {entry.inode} then {path.stat().st_ino}" in error.message
        assert not outcome.observations


# -- the walk itself ------------------------------------------------------------------------------


def test_a_regular_file_beneath_its_root_is_opened_and_named_by_its_path(tmp_path: Path) -> None:
    pool, _ = _pool(tmp_path)
    path = pool / "zib" / "kelt.txt"
    found = path.stat()
    with open_beneath(path, pool, device=found.st_dev, inode=found.st_ino) as handle:
        assert handle.read() == INSIDE.encode()
        assert handle.name == str(path)
    assert stat_beneath(path, pool).st_ino == found.st_ino
    # The root is taken as declared, a link in its own spelling and all.
    (tmp_path / "pool_link").symlink_to(pool, target_is_directory=True)
    with open_beneath(tmp_path / "pool_link" / "zib" / "kelt.txt", tmp_path / "pool_link") as held:
        assert held.read() == INSIDE.encode()


def test_a_link_on_the_way_or_in_place_of_the_file_is_refused(tmp_path: Path) -> None:
    pool, outside = _pool(tmp_path)
    (pool / "vorn").symlink_to(outside, target_is_directory=True)
    (pool / "zib" / "flink.txt").symlink_to(outside / "kelt.txt")
    for path, said in (
        (pool / "vorn" / "kelt.txt", "passes through a link at vorn"),
        (pool / "zib" / "flink.txt", "it is now a link (zib/flink.txt)"),
    ):
        with pytest.raises(NotTheRecordedFileError, match=re.escape(said)):
            open_beneath(path, pool)
        with pytest.raises(NotTheRecordedFileError, match=re.escape(said)):
            stat_beneath(path, pool)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes here")
def test_what_is_not_a_regular_file_is_refused_and_a_pipe_is_never_waited_on(
    tmp_path: Path,
) -> None:
    pool, _ = _pool(tmp_path)
    os.mkfifo(pool / "zib" / "pipe.txt")
    for path in (pool / "zib" / "pipe.txt", pool / "zib"):
        with pytest.raises(NotTheRecordedFileError, match="not a regular file"):
            open_beneath(path, pool)
        with pytest.raises(NotTheRecordedFileError, match="not a regular file"):
            stat_beneath(path, pool)


def test_a_file_whose_inode_or_device_is_not_the_recorded_one_is_refused(tmp_path: Path) -> None:
    pool, _ = _pool(tmp_path)
    path = pool / "zib" / "kelt.txt"
    found = path.stat()
    with pytest.raises(NotTheRecordedFileError, match="its inode is"):
        open_beneath(path, pool, inode=found.st_ino + 1)
    with pytest.raises(NotTheRecordedFileError, match="its device is"):
        open_beneath(path, pool, device=found.st_dev + 1)
    # A device the Index stores as the signed reading of its bits is the same device.
    signed = found.st_dev - (1 << 64) if found.st_dev >= (1 << 63) else found.st_dev
    open_beneath(path, pool, device=signed, inode=found.st_ino).close()


def test_the_walk_without_descriptors_refuses_a_junction_and_a_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows walks by ``lstat`` (no ``O_NOFOLLOW``, no ``openat``) and asks each folder whether
    it is a junction, which ``lstat`` calls a folder. Junctions cannot be made here, so the check
    is told that ``zib`` is one, and the Windows walk is taken on this system."""
    pool, _ = _pool(tmp_path)
    path = pool / "zib" / "kelt.txt"
    monkeypatch.setattr(platform, "_WALKS_BY_DESCRIPTOR", False)
    with open_beneath(path, pool) as handle:
        assert handle.read() == INSIDE.encode()

    monkeypatch.setattr(platform, "is_junction", lambda found: Path(found).name == "zib")
    for reach in (open_beneath, stat_beneath):
        with pytest.raises(NotTheRecordedFileError, match="passes through a junction at zib"):
            reach(path, pool)
    monkeypatch.undo()

    # The file opened must be the one looked at: a swap between the two is refused.
    monkeypatch.setattr(platform, "_WALKS_BY_DESCRIPTOR", False)
    other = pool / "zib" / "other.txt"
    other.write_text(INSIDE)
    monkeypatch.setattr(platform, "_walk_by_lstat", lambda *_: (str(path), other.lstat()))
    with pytest.raises(NotTheRecordedFileError, match="not the one looked at"):
        open_beneath(path, pool)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows makes junctions")
def test_where_the_system_makes_no_junctions_nothing_is_asked_of_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off Windows the junction check answers no without a call, so a walk is what it was. The
    project's own layout asks ``os.path.isjunction`` of its own folders (``storage/layout.py``),
    so what is checked is that nothing in the root is asked."""
    pool, outside = _pool(tmp_path)
    (pool / "vorn").symlink_to(outside, target_is_directory=True)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    real = os.path.isjunction
    asked: list[Path] = []

    def spied(path: str | os.PathLike[str]) -> bool:
        asked.append(Path(path))
        return real(path)

    monkeypatch.setattr(os.path, "isjunction", spied)
    with os.scandir(pool) as listing:
        assert not [item.name for item in listing if platform.is_junction(item)]
    assert platform.is_junction(pool / "zib") is False
    open_beneath(pool / "zib" / "kelt.txt", pool).close()
    run_census(project, run_id=CENSUS, started_at="t")
    assert not [path for path in asked if path.is_relative_to(pool)]
    with open_catalog(project.path) as catalog:
        types = dict(catalog.query("SELECT relative_path, entry_type FROM entries").fetchall())
    assert types["zib"] == EntryType.DIRECTORY.value
    assert types["zib/kelt.txt"] == EntryType.FILE.value
    assert types["vorn"] == EntryType.SYMLINK.value
