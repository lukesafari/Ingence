"""Every DuckDB connection the engine opens, opened here: nothing is fetched, nothing loads itself.

The engine runs only on the user's machine. DuckDB installs a "known extension" from its own server
the first time a query or a setting needs one that is not on the machine
(``autoinstall_known_extensions``, on by default). Every connection is opened here with automatic
installation off, and a test holds every module to this function.

Nothing loads itself either, and nothing is read from the person's own DuckDB folder. With
installation off, an extension already in ``~/.duckdb/extensions`` still loaded itself the first
time a query needed it (``autoload_known_extensions``). So what the engine ran depended on what any
other DuckDB had once installed on the machine. Automatic loading is off, and the connection reads
extensions from a folder of its own (:data:`EXTENSION_DIRECTORY`), which the engine never writes
and so stays empty. What DuckDB's own build carries (Parquet, JSON, the time-zone support) is part
of the build and loads as usual.

A time zone given in the settings when a connection opens would fetch an extension. DuckDB reads
that zone before the build's own time-zone support is registered, so it looks for the support among
installed extensions, finds none and installs it from its server. The Graph workspace therefore
sets the zone after the connection opens (``api/v1/explorer/projection.py``), where the build's own
support answers. A zone given at opening fails here in words and fetches nothing.

Nothing spills beside wherever the process started. An in-memory connection spills a query larger
than its memory limit to ``temp_directory``, which is ``.tmp`` relative to the working directory by
default. A Dashboard or a command started inside a root would write there. Ingence never writes
inside the folders it reads. The connection spills into a folder of the system's own temporary
directory instead, one private to the person and checked before it is used
(:func:`spill_directory`).
"""

from __future__ import annotations

import functools
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

__all__ = [
    "EXTENSION_DIRECTORY",
    "SPILL_DIRECTORY",
    "arrow_reader",
    "arrow_table",
    "connect",
    "spill_directory",
]


def _named_for_this_person() -> Path:
    """The spill folder's name: in the system's temporary folder, and this person's alone.

    On Linux the temporary folder (``/tmp``) is shared by everybody on the machine, a lab server or
    a cluster's login node among them. A fixed name there could be made first by somebody else, as
    a link into a folder of data, and every spill would then be written there; or made first by a
    colleague, and nobody else could spill at all. So the name carries the person's user number,
    and :func:`spill_directory` checks the folder before any connection uses it.
    """
    owner = os.getuid() if hasattr(os, "getuid") else "user"
    return Path(tempfile.gettempdir()) / f"ingence-duckdb-{owner}"


#: Where a connection spills a query larger than its memory, where that folder passes
#: :func:`spill_directory`'s check.
SPILL_DIRECTORY = _named_for_this_person()

#: Where a connection looks for extensions: a folder of the engine's own that nothing writes
#: (DuckDB creates it only to install, and the engine installs nothing), never ``~/.duckdb``.
EXTENSION_DIRECTORY = SPILL_DIRECTORY / "no-extensions"


@functools.cache
def spill_directory() -> Path:
    """The folder connections spill into, made private to this person and checked.

    It must be a real folder (never a link), owned by this person, and writable by nobody else.
    One that fails (somebody else made it, or made it a link) is never used: the process spills
    into a new private folder of its own instead (``tempfile.mkdtemp``, made with this person's
    permissions alone), so a planted link never receives a spill and nobody is kept from spilling.
    """
    try:
        os.mkdir(SPILL_DIRECTORY, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return _private_for_this_process()
    try:
        status = os.lstat(SPILL_DIRECTORY)
    except OSError:
        return _private_for_this_process()
    trusted = (
        stat.S_ISDIR(status.st_mode)
        and not os.path.isjunction(SPILL_DIRECTORY)
        and (not hasattr(os, "getuid") or status.st_uid == os.getuid())
        and (os.name == "nt" or not status.st_mode & 0o022)
    )
    if trusted:
        return SPILL_DIRECTORY
    return _private_for_this_process()


def _private_for_this_process() -> Path:
    """A new spill folder only this person can use, removed when the process ends."""
    import atexit
    import shutil

    made = Path(tempfile.mkdtemp(prefix="ingence-duckdb-"))
    atexit.register(shutil.rmtree, made, ignore_errors=True)
    return made


def connect(database: str = ":memory:", **config: Any) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection that installs and loads nothing: ``config`` adds to the settings."""
    spill = spill_directory()
    settings = {
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "extension_directory": str(spill / "no-extensions"),
        "temp_directory": str(spill),
        **config,
    }
    return duckdb.connect(database, config=settings)


# ---------------------------------------------------------------------------
# A result as Arrow, by whichever name the installed DuckDB gives it.
#
# DuckDB 1.4 named the two Arrow fetches ``to_arrow_table`` and ``to_arrow_reader``, and 1.5 warns
# on the person's screen at the older names (``fetch_arrow_table``, ``fetch_record_batch``). DuckDB
# 1.0 has only the older ones, and a census on it stopped at its first compaction with an
# ``AttributeError``. Every fetch goes through these two, which take the newer name where it is and
# the older one where it is not, so every version the package allows reads a run the same way.
# ---------------------------------------------------------------------------


def arrow_table(result: Any) -> pa.Table:
    """A query's whole result as an Arrow table (``to_arrow_table``, or its older name)."""
    fetch = getattr(result, "to_arrow_table", None) or result.fetch_arrow_table
    table: pa.Table = fetch()
    return table


def arrow_reader(result: Any, rows: int) -> pa.RecordBatchReader:
    """A query's result as a stream of Arrow batches of at most ``rows`` rows (``to_arrow_reader``,
    or its older name)."""
    read = getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
    reader: pa.RecordBatchReader = read(rows)
    return reader
