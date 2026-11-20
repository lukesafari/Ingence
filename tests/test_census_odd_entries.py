"""One odd entry is one entry's fact, never a census that publishes nothing or leaves the root.

Three entries the census used to mishandle. A Windows junction is a link that ``lstat`` calls a
folder, and a census that took it for one walked out of the root through it. It is recorded as
the link it is and never walked into. A name that is not valid UTF-8, as Linux hands it to Python
(``b"bad\\xff.txt"`` becomes ``"bad\\udcff.txt"``), cannot be stored as text or identified, and the
strict encoding under every identifier ended the whole census. Such a name is recorded as a scan
error and left out with everything beneath it, and every ordinary name keeps exactly the
identifier it had. And any failure the walk did not foresee on one name is recorded against that
name, and the walk goes on.

Junctions cannot be made on macOS or Linux, and a filesystem there that refuses such names
(APFS) cannot hold an undecodable one, so both are simulated: the junction check is told which
folder is a junction, and the listing is wrapped to hand a real file over under an undecodable
name. Every name is invented.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from ingence.core.ids import entry_id
from ingence.core.project import Project
from ingence.core.schema import FailureClass, SymlinkState
from ingence.scan import census
from ingence.scan.census import follow_link_chain, outside_root, run_census
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
REBIND = "run_20260101T000002Z_222222222222"

#: What Linux gives Python for the byte name ``b"bad\xffname.txt"``.
UNDECODABLE = os.fsdecode(b"bad\xffname.txt") if os.name != "nt" else "bad\udcffname.txt"
#: How the scan error spells it: each character UTF-8 cannot carry written as an escape.
ESCAPED = "bad\\udcffname.txt"


def _pool(base: Path) -> tuple[Path, Path]:
    pool = base / "pool"
    (pool / "zib").mkdir(parents=True)
    (pool / "zib" / "kelt_01.csv").write_text("a,b\n1,2\n")
    (pool / "kelt_02.csv").write_text("a,b\n3,4\n")
    outside = base / "outside"
    outside.mkdir()
    (outside / "vorn.csv").write_text("a,b\n")
    return pool, outside


def _entries(project: Project, run_id: str = CENSUS) -> dict[str, tuple[str, str, str | None]]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        rows = catalog.query(
            "SELECT relative_path, entry_type, symlink_state, symlink_target FROM entries"
        ).fetchall()
    return {path: (kind, state, target) for path, kind, state, target in rows}


def _errors(project: Project) -> list[tuple[str | None, str, str, str, str, str]]:
    with open_catalog(project.path) as catalog:
        return catalog.query(
            "SELECT entry_id, relative_path, operation, error_type, failure_class, message "
            "FROM scan_errors ORDER BY relative_path"
        ).fetchall()


# -- junctions ------------------------------------------------------------------------------------


def test_a_junction_is_recorded_as_a_link_and_never_walked_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``jun`` names a folder outside the root and ``jin`` one inside it. Here both are real
    folders holding a file, which is what a junction looks like to ``lstat``. Neither is
    listed, looked into or recognised, and a rebind keeps both links."""
    pool, outside = _pool(tmp_path)
    for name in ("jun", "jin"):
        (pool / name).mkdir()
        (pool / name / "inner.txt").write_text("what the junction names\n")
    texts = {"jun": str(outside), "jin": str(pool / "zib")}
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])

    def junction(item: object) -> bool:
        path = item.path if isinstance(item, os.DirEntry) else item  # type: ignore[attr-defined]
        return Path(os.fspath(path)).name in texts  # type: ignore[arg-type]

    real_readlink, real_scandir = os.readlink, os.scandir
    real_recognise = census.recognise_directory
    listed: list[Path] = []
    recognised: list[Path] = []

    def readlink(path: str | os.PathLike[str], *args: object, **kwargs: object) -> str:
        name = Path(os.fspath(path)).name
        return texts[name] if name in texts else real_readlink(path, *args, **kwargs)  # type: ignore[arg-type]

    def scandir(path: str | os.PathLike[str] = ".") -> object:
        listed.append(Path(os.fspath(path)))
        return real_scandir(path)

    def recognise(path: Path, *args: object, **kwargs: object) -> object:
        recognised.append(path)
        return real_recognise(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(census, "is_junction", junction)
    monkeypatch.setattr(os, "readlink", readlink)
    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(census, "recognise_directory", recognise)

    run_census(project, run_id=CENSUS, started_at="t")
    run_census(project, run_id=REBIND, started_at="t", rebind=True)
    for run_id in (CENSUS, REBIND):
        entries = _entries(project, run_id)
        assert entries["jun"] == ("symlink", SymlinkState.OUTSIDE_ROOT.value, str(outside)), run_id
        assert entries["jin"] == ("symlink", SymlinkState.RESOLVES.value, str(pool / "zib")), run_id
        assert not [path for path in entries if path.startswith(("jun/", "jin/"))], run_id
        assert entries["zib/kelt_01.csv"][0] == "file"
    assert not {pool / "jun", pool / "jin"} & set(listed)
    assert not {pool / "jun", pool / "jin"} & set(recognised)


def test_a_junction_on_a_chain_s_way_is_a_hop_read_from_its_text() -> None:
    """A link's chain walked through a junction as through a folder would look at whatever the
    junction names. Each folder on the way is asked whether it is one, and a junction's text is
    read as a link's is, so a junction out of the root ends the chain there."""
    root = "C:\\data\\pool"
    folders = {root, "C:\\data\\pool\\zib", "C:\\data\\pool\\jun", "C:\\data\\pool\\jin"}
    files = {"C:\\data\\pool\\zib\\kelt.csv"}
    junctions = {
        "C:\\data\\pool\\jun": "\\??\\C:\\data\\elsewhere",
        "C:\\data\\pool\\jin": "\\??\\C:\\data\\pool\\zib",
    }
    looked: list[str] = []

    def lstat(path: str) -> SimpleNamespace:
        looked.append(path)
        if path in folders:
            return SimpleNamespace(st_mode=stat_module.S_IFDIR)
        if path in files:
            return SimpleNamespace(st_mode=stat_module.S_IFREG)
        raise FileNotFoundError(path)

    def leaves(link: str, target: str) -> bool:
        return outside_root(link, target, root, case_insensitive=True, windows=True)

    for target, expected in (
        ("jun\\kelt.csv", SymlinkState.OUTSIDE_ROOT),
        ("jin\\kelt.csv", SymlinkState.RESOLVES),
        ("jin\\gone.csv", SymlinkState.BROKEN),
        ("zib\\kelt.csv", SymlinkState.RESOLVES),
    ):
        found = follow_link_chain(
            "C:\\data\\pool\\l",
            target,
            root,
            leaves=leaves,
            case_insensitive=True,
            windows=True,
            lstat=lstat,
            readlink=junctions.__getitem__,
            junction=junctions.__contains__,
        )
        assert found is expected, target
    assert not [path for path in looked if path.startswith("C:\\data\\elsewhere")]
    assert "C:\\data\\pool\\jun\\kelt.csv" not in looked


# -- a name that is not text ----------------------------------------------------------------------


class _Undecodable:
    """A listed entry over a real one, under the name Linux gives a byte name that is not UTF-8."""

    def __init__(self, inner: os.DirEntry[str], name: str) -> None:
        self._inner = inner
        self.name = name
        self.path = os.path.join(os.path.dirname(inner.path), name)

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        return self._inner.stat(follow_symlinks=follow_symlinks)

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return self._inner.is_dir(follow_symlinks=follow_symlinks)

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return self._inner.is_file(follow_symlinks=follow_symlinks)

    def is_symlink(self) -> bool:
        return False

    def is_junction(self) -> bool:
        return False

    def inode(self) -> int:
        return self._inner.inode()


def _listing_with_undecodable_names(
    monkeypatch: pytest.MonkeyPatch, pool: Path, names: dict[str, str]
) -> None:
    """Wrap the listing of ``pool`` so each real name in ``names`` is handed over as its value."""
    real = os.scandir

    class Listing:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __enter__(self) -> Listing:
            return self

        def __exit__(self, *_: object) -> None:
            self._inner.close()  # type: ignore[attr-defined]

        def __iter__(self) -> object:
            for item in self._inner:  # type: ignore[attr-defined]
                yield _Undecodable(item, names[item.name]) if item.name in names else item

    def scandir(path: str | os.PathLike[str] = ".") -> object:
        listing = real(path)
        if os.path.realpath(os.fspath(path)) == os.path.realpath(pool):
            return Listing(listing)
        return listing

    monkeypatch.setattr(os, "scandir", scandir)


def test_a_name_that_is_not_text_is_a_scan_error_and_the_census_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = _pool(tmp_path)
    (pool / "real.txt").write_text("the census saw this\n")
    (pool / "realdir").mkdir()
    (pool / "realdir" / "inner.txt").write_text("beneath a folder whose name is not text\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    folder = UNDECODABLE.replace("name.txt", "dir")
    _listing_with_undecodable_names(monkeypatch, pool, {"real.txt": UNDECODABLE, "realdir": folder})

    run_census(project, run_id=CENSUS, started_at="t")

    entries = _entries(project)
    # Everything else is indexed, under exactly the identifier it always had.
    assert entries["kelt_02.csv"][0] == "file"
    assert entries["zib/kelt_01.csv"][0] == "file"
    with open_catalog(project.path) as catalog:
        (kelt,) = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'kelt_02.csv'"
        ).fetchone()
    root = project.identity.roots[0]
    assert kelt == entry_id(root.root_id, "kelt_02.csv", (), case_sensitivity=root.case_sensitivity)
    # Neither odd name has an entry, and nothing beneath the folder was walked.
    assert not [path for path in entries if "bad" in path or "inner" in path]
    errors = {row[1]: row for row in _errors(project)}
    for spelled in (ESCAPED, ESCAPED.replace("name.txt", "dir")):
        identifier, _, operation, kind, failure, message = errors[spelled]
        assert (operation, kind, failure) == (
            "name",
            "UndecodableName",
            FailureClass.COMPREHENSION.value,
        )
        assert identifier == entry_id(
            root.root_id, spelled, (), case_sensitivity=root.case_sensitivity
        )
        assert spelled in message
        assert "renaming it" in message


# -- a failure the walk did not foresee ----------------------------------------------------------


def test_an_unforeseen_failure_on_one_name_is_recorded_and_the_walk_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = _pool(tmp_path)
    (pool / "vorn").mkdir()
    (pool / "vorn" / "beneath.txt").write_text("never reached\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    real = census.recognise_directory

    def recognise(path: Path, *args: object, **kwargs: object) -> object:
        if path.name == "vorn":
            message = "an invented fault on one folder"
            raise RuntimeError(message)
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(census, "recognise_directory", recognise)
    run_census(project, run_id=CENSUS, started_at="t")

    entries = _entries(project)
    assert "vorn" not in entries
    assert "vorn/beneath.txt" not in entries
    assert entries["zib/kelt_01.csv"][0] == "file"
    ((_, relative, operation, kind, failure, message),) = _errors(project)
    assert (relative, operation, kind, failure) == (
        "vorn",
        "entry",
        "RuntimeError",
        FailureClass.COMPREHENSION.value,
    )
    assert message == "an invented fault on one folder"


def test_an_unforeseen_failure_reading_one_name_keeps_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the entry is built (reading its name) keeps the entry as its stat said."""
    pool, _ = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    real = census._Walker.read_name_of

    def read_name_of(self: object, entry: object, records: object) -> object:
        if entry.name == "kelt_02.csv":  # type: ignore[attr-defined]
            message = "an invented fault reading one name"
            raise ValueError(message)
        return real(self, entry, records)  # type: ignore[arg-type]

    monkeypatch.setattr(census._Walker, "read_name_of", read_name_of)
    run_census(project, run_id=CENSUS, started_at="t")

    entries = _entries(project)
    assert entries["kelt_02.csv"][0] == "file"
    assert entries["zib/kelt_01.csv"][0] == "file"
    ((_, relative, operation, kind, _, _),) = _errors(project)
    assert (relative, operation, kind) == ("kelt_02.csv", "read_name", "ValueError")
