"""The folder a file lies in, one hop up, and the acquisition its own name lies beside.

People put the distinguishing part of a name in the file and the shared part in the folder above
it: ``proc/A549_vehicle_002.tif`` is processed without saying so. A rule that reads what a file's
folder states (``stage_inherited_from_folder``) is handed the folder's name, cut into components
and bound by the Dictionary, beside the file's own. The hop is one and never more, as the rule's
declaration says.

An instrument writes a file of metadata beside each acquisition, under the acquisition's own name
(``A549_001.nd2`` and ``A549_001.xml``). That file is part of the acquisition and is not named for
it the way an analysis tool names what it writes. So each file is handed the acquisition beside it
in its own folder whose name is the file's but for the extension, read from names and the spellings
the Dictionary declares an instrument writes. No file's content is read.

The work is linear in the pool: one pass indexes each folder's records and its acquisitions' names,
and one hands every entry what its folder holds.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ingence.core.schema import EntryType

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from ingence.infer.subjects import EntryRecords

__all__ = ["stem_of", "with_enclosing_folders"]


def stem_of(name: str, extension: str | None) -> str:
    """A file's name without its extension, casefolded: what two files of one name share."""
    folded = name.casefold()
    suffix = f".{extension.casefold()}" if extension else ""
    if suffix and folded.endswith(suffix) and len(folded) > len(suffix):
        return folded[: -len(suffix)]
    return folded


def _extension(held: EntryRecords) -> str:
    """The spelling a file's format is read by: the compound where there is one."""
    entry = held.entry
    return (entry.compound_extension or entry.extension or "").casefold()


def with_enclosing_folders(
    records: Sequence[EntryRecords], acquisition_spellings: Collection[str]
) -> tuple[EntryRecords, ...]:
    """Hand every entry its enclosing folder's name records and the acquisition beside it.

    ``acquisition_spellings`` are the spellings, casefolded, that the Dictionary declares an
    instrument writes as it acquires. A file in one of them is an acquisition and is handed none
    beside it. A file is handed the acquisition beside it only where exactly one file of its
    folder in such a spelling has its name but for the extension, so two acquisitions of one name
    in two formats leave it with none.
    """
    spellings = frozenset(spelling.casefold() for spelling in acquisition_spellings)
    folders: dict[str, EntryRecords] = {
        held.entry.entry_id: held
        for held in records
        if held.entry.entry_type is EntryType.DIRECTORY
    }
    acquired: dict[tuple[str | None, str], list[str]] = {}
    for held in records:
        entry = held.entry
        if entry.entry_type is EntryType.DIRECTORY or entry.parent_entry_id is None:
            continue
        extension = _extension(held)
        if extension and extension in spellings:
            stem = stem_of(entry.name, entry.compound_extension or entry.extension)
            acquired.setdefault((entry.parent_entry_id, stem), []).append(entry.relative_path)
    result = []
    for held in records:
        entry = held.entry
        folder = folders.get(entry.parent_entry_id) if entry.parent_entry_id else None
        beside = None
        own = entry.entry_type is not EntryType.DIRECTORY and _extension(held) in spellings
        if entry.entry_type is not EntryType.DIRECTORY and not own:
            stem = stem_of(entry.name, entry.compound_extension or entry.extension)
            found = acquired.get((entry.parent_entry_id, stem), [])
            beside = found[0] if len(found) == 1 else None
        if folder is None and beside is None and not own:
            result.append(held)
            continue
        result.append(
            replace(
                held,
                enclosing_folder=folder.entry.relative_path if folder is not None else None,
                enclosing_components=folder.components if folder is not None else (),
                enclosing_bindings=folder.bindings if folder is not None else (),
                acquisition_beside=beside,
                acquired=own,
            )
        )
    return tuple(result)
