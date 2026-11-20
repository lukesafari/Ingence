"""Drafts: every edit saved within a second, applied only when a person commits.

Each draft kind is one file, replaced atomically: the writer writes a sibling, flushes it and calls
``os.replace``, the protocol of the pointer's own ``storage/commit.write_pointer``. So a reader sees
the whole old draft or the whole new one, and a crash in the middle of a save loses at most the
last keystroke.

A save adds to the draft and never replaces it. The values a save sends are laid over the draft as
it stands, and a value leaves the draft only where the save names it in ``removed`` (the person put
it back as it was). So a save that sends nothing changes nothing. A page that changed view before
its timer fired, and found no field to read, cannot erase what was typed.

The kinds, and what committing one does:

- ``settings``: a partial ``Settings`` dict, holding only the fields a person changed. A commit
  writes it into ``settings.dashboard.yaml`` and validates it with ``settings.yaml`` beneath it.
- ``rule_parameters``: ``{rule_id: {parameter: value}}``. A commit writes it as a new project
  Rulebook revision of ``override`` changes (append-only).
- ``views``: page and graph view state. A commit never writes it. A view belongs to a person and
  not to the project, and stays a draft.
- ``notes``: unsent note text by target. Nothing happens until a note is sent, and a sent note is
  a Record event at once.

Saving and committing take turns. One lock, :func:`writing`, is held by a save and by a whole
commit, and a commit clears a draft only where the draft is still the one the commit read. Without
the lock, a save made while a commit ran would be neither committed nor kept. It is this process's
lock (:data:`WRITING`) and a lock on a file the operating system holds (``workspace/drafts.lock``),
so two programs serving one project take turns too.

A save that arrives after a newer one from the same page is not applied. Requests can arrive out
of order: a page that saved 3 and then 7 could have the 7 land first and the 3 land after it,
leaving 3 in the draft while the page said "saved". So a page names itself (``writer``) and numbers
its saves (``sequence``), and the draft keeps the last number each page sent. A save numbered at or
below it is stale: it changes nothing and says so (``Draft.stale``). The newer save already held
everything the older one did, because a page sends every edit not yet saved. Every save that is
applied raises the draft's ``revision``, so a page can tell when another page or program saved the
same kind in between.

A draft is validated before it is saved, and a save never refuses it. An edit that fails validation
(a negative worker count) is saved with its error beside it, so the page can show the error and
the person's typing is not lost. Only a commit refuses it.
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import write_atomically
from ingence.storage.layout import owned_path
from ingence.workspace import WORKSPACE_DIR, WORKSPACE_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

__all__ = [
    "COMMITTED_KINDS",
    "DRAFT_KINDS",
    "WRITING",
    "Draft",
    "clear_draft",
    "read_draft",
    "save_draft",
    "settings_errors",
    "uncommitted",
    "writing",
]

DRAFT_KINDS = ("settings", "rule_parameters", "views", "notes")

#: The kinds a commit applies; the others are a person's own and never "uncommitted".
COMMITTED_KINDS = ("settings", "rule_parameters")

#: Held by every save and by a whole commit, so the two take turns in this process.
WRITING = threading.RLock()

#: The file whose lock the operating system holds while a save, a clear or a commit runs, so two
#: programs serving one project take turns as two threads do.
LOCK_FILE = "drafts.lock"

#: How long a save waits for another program's save or commit before it says so.
LOCK_TIMEOUT_SECONDS = 30.0

#: How many pages' last save numbers a draft keeps. A page that has not saved since this many others
#: did is forgotten, and its next save is applied as a first one.
WRITERS_KEPT = 64

_DEPTH = threading.local()


@contextlib.contextmanager
def writing(project: Path) -> Iterator[None]:
    """Hold the drafts' lock: this process's (:data:`WRITING`), then the file's.

    Taken again inside itself (a commit clears the drafts it applied), only the outermost hold
    takes the file's lock, because the operating system's lock would refuse a second open of the
    file by the same process.
    """
    from ingence.jobs.locks import FileLock

    with WRITING:
        depth = getattr(_DEPTH, "held", 0)
        if depth:
            _DEPTH.held = depth + 1
            try:
                yield
            finally:
                _DEPTH.held = depth
            return
        lock = FileLock(owned_path(project, WORKSPACE_DIR, LOCK_FILE))
        if not lock.acquire(timeout=LOCK_TIMEOUT_SECONDS, poll=0.02):
            message = (
                "another program serving this project is saving or committing drafts, and has "
                f"for more than {LOCK_TIMEOUT_SECONDS:g} s: try again in a moment"
            )
            raise TimeoutError(message)
        _DEPTH.held = 1
        try:
            yield
        finally:
            _DEPTH.held = 0
            lock.release()


@dataclass
class Draft:
    """One kind of draft: its values, when it was last saved, and any validation errors."""

    kind: str
    values: dict[str, Any] = field(default_factory=dict)
    saved_at: str = ""
    errors: dict[str, str] = field(default_factory=dict)
    version: str = WORKSPACE_VERSION
    #: Raised by one with every save applied (and every clear), never lowered.
    revision: int = 0
    #: Each page's last save number, by the name the page gave itself.
    writers: dict[str, int] = field(default_factory=dict)
    #: Set on what :func:`save_draft` returns when the save was older than one already applied
    #: from the same page, and so changed nothing. Never saved.
    stale: bool = False

    @property
    def empty(self) -> bool:
        return not self.values


def _path(project: Path, kind: str) -> Path:
    if kind not in DRAFT_KINDS:
        message = f"no draft kind {kind!r}; the kinds are {', '.join(DRAFT_KINDS)}"
        raise ValueError(message)
    return project / WORKSPACE_DIR / "drafts" / f"{kind}.json"


def read_draft(project: Path, kind: str) -> Draft:
    path = _path(project, kind)
    if not path.is_file():
        return Draft(kind=kind)
    loaded = json.loads(path.read_text("utf-8"))
    return Draft(
        kind=kind,
        values=dict(loaded.get("values", {})),
        saved_at=str(loaded.get("saved_at", "")),
        errors=dict(loaded.get("errors", {})),
        version=str(loaded.get("version", WORKSPACE_VERSION)),
        revision=_whole(loaded.get("revision")),
        writers=_writers(loaded.get("writers")),
    )


def _writers(stated: object) -> dict[str, int]:
    """Each page's last save number, oldest saver first, from a draft's file (a list of pairs,
    so that the order survives the file's sorted keys)."""
    found: dict[str, int] = {}
    for pair in stated if isinstance(stated, list) else ():
        if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str):
            found[pair[0]] = _whole(pair[1])
    return found


def _whole(value: object) -> int:
    """A count read from a draft's file: a whole number, or 0 for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def settings_errors(project: Path, values: dict[str, Any]) -> dict[str, str]:
    """Each settings field that would fail ``Settings`` once laid over the files in force."""
    from pydantic import ValidationError

    from ingence.core.settings import Settings
    from ingence.workspace.settings_overlay import merged, read_layers

    try:
        Settings.model_validate(merged(read_layers(project).effective, values))
    except ValidationError as error:
        return {
            ".".join(str(part) for part in item["loc"]): str(item["msg"]) for item in error.errors()
        }
    return {}


def _key_path(removed: str | Sequence[str]) -> tuple[str, ...]:
    """A value's place in a draft: a dotted name (``scan.workers``) or its keys in order."""
    if isinstance(removed, str):
        return tuple(removed.split("."))
    return tuple(str(key) for key in removed)


def _without(values: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """``values`` with the value at ``keys`` taken out, and any mapping it leaves empty."""
    if not keys or keys[0] not in values:
        return values
    head, rest = keys[0], keys[1:]
    kept = dict(values)
    if not rest:
        del kept[head]
        return kept
    inner = kept[head]
    if not isinstance(inner, dict):
        return values
    trimmed = _without(inner, rest)
    if trimmed:
        kept[head] = trimmed
    else:
        del kept[head]
    return kept


def save_draft(
    project: Path,
    kind: str,
    values: dict[str, Any],
    *,
    removed: Sequence[str | Sequence[str]] = (),
    now: str | None = None,
    writer: str | None = None,
    sequence: int | None = None,
) -> Draft:
    """Lay ``values`` over one kind's draft, take out ``removed``, and save it atomically.

    The whole draft is checked after the change, and its errors are saved beside it. A save
    that sends no value and removes none changes nothing and writes nothing.

    A save from a page names the page (``writer``) and numbers the save (``sequence``). One numbered
    at or below the last that page's saves reached is stale: nothing is written, and the draft as
    it stands comes back with ``stale`` set.
    """
    _path(project, kind)
    with writing(project):
        return _save(
            project, kind, values, removed=removed, now=now, writer=writer, sequence=sequence
        )


def _save(
    project: Path,
    kind: str,
    values: dict[str, Any],
    *,
    removed: Sequence[str | Sequence[str]],
    now: str | None,
    writer: str | None,
    sequence: int | None,
) -> Draft:
    from ingence.workspace.settings_overlay import merged

    # Check that the path to the project's own drafts folder holds no link, before anything is read
    # or written there (``storage.layout.owned_path``).
    path = owned_path(project, _path(project, kind))
    current = read_draft(project, kind)
    numbered = writer is not None and sequence is not None
    if numbered and current.writers.get(str(writer), 0) >= int(sequence or 0):
        return replace(current, stale=True)
    if not values and not removed:
        return current
    combined = merged(current.values, values)
    for place in removed:
        combined = _without(combined, _key_path(place))
    errors: dict[str, str] = {}
    if kind == "settings":
        errors = settings_errors(project, combined)
    elif kind == "rule_parameters":
        from ingence.workspace.revisions import check_overrides

        errors = check_overrides(project, combined)
    writers = dict(current.writers)
    if numbered:
        # The page moves to the end, so the pages kept are those that saved last.
        writers.pop(str(writer), None)
        writers[str(writer)] = int(sequence or 0)
        while len(writers) > WRITERS_KEPT:
            writers.pop(next(iter(writers)))
    draft = Draft(
        kind=kind,
        values=combined,
        saved_at=now or datetime.now(tz=UTC).isoformat(timespec="seconds"),
        errors=errors,
        revision=current.revision + 1,
        writers=writers,
    )
    _write(path, draft)
    return draft


def _write(path: Path, draft: Draft) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(
        path,
        json.dumps(
            {
                "version": draft.version,
                "kind": draft.kind,
                "saved_at": draft.saved_at,
                "values": draft.values,
                "errors": draft.errors,
                "revision": draft.revision,
                "writers": [[name, number] for name, number in draft.writers.items()],
            },
            sort_keys=True,
            indent=2,
        ),
    )


def clear_draft(project: Path, kind: str, *, if_unchanged: Draft | None = None) -> bool:
    """Remove one kind's draft after a commit applied it; say whether it was removed.

    With ``if_unchanged``, only where the draft is still the one the commit read (the same values,
    saved at the same moment, the same revision): a save made since, even by another process, is
    kept.

    A draft that pages numbered their saves into is emptied and not removed: it keeps each page's
    last number, so a page's save still on its way when the commit ran is known as stale when it
    lands, and does not put a committed value back as a draft.
    """
    with writing(project):
        path = owned_path(project, _path(project, kind))
        now = read_draft(project, kind)
        if if_unchanged is not None and (now.values, now.saved_at, now.revision) != (
            if_unchanged.values,
            if_unchanged.saved_at,
            if_unchanged.revision,
        ):
            return False
        if now.writers:
            _write(
                path,
                Draft(
                    kind=kind,
                    saved_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
                    revision=now.revision + 1,
                    writers=now.writers,
                ),
            )
        else:
            path.unlink(missing_ok=True)
        return True


def uncommitted(project: Path) -> dict[str, int]:
    """How many edits each committed kind holds: the number on the Commit button."""
    counts: dict[str, int] = {}
    for kind in COMMITTED_KINDS:
        draft = read_draft(project, kind)
        if kind == "rule_parameters":
            # A value that is not a rule's parameters is counted, so its error is seen.
            counts[kind] = sum(
                len(values) if isinstance(values, dict) else 1 for values in draft.values.values()
            )
        else:
            counts[kind] = _leaf_count(draft.values)
    return counts


def _leaf_count(values: Any) -> int:
    if isinstance(values, dict):
        return sum(_leaf_count(value) for value in values.values())
    return 1
