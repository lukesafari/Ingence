"""What each operating system gives a walk: a birth time, file numbers, case, links.

This module says, per system, what a directory listing and a ``stat`` carry. It also makes the one
``stat`` a walk makes per entry. The census reads each system's facts here instead of testing
``sys.platform`` or ``os.name`` itself, so the engine behaves the same across systems.

=========  ===============================  ==================  ===================  ===========
system     birth time                       file numbers        case (a default)     links
=========  ===============================  ==================  ===================  ===========
macOS      ``stat`` (``st_birthtime``)      in the listing      insensitive          symbolic
Linux      ``statx``, where the filesystem  in the listing      sensitive            symbolic
           keeps one (not NFS, some FUSE)
Windows    ``stat`` (``st_birthtime``,      not in the          insensitive          symbolic,
           Python 3.12)                     listing                                  junctions
=========  ===============================  ==================  ===================  ===========

- Birth time on Linux is read with ``statx`` (glibc 2.28 or later, through ``ctypes``, because
  Python 3.12's ``stat`` gives none). The call takes the place of the ``lstat`` the census already
  makes per entry, so no call is added. It uses the flags that ``lstat`` uses in the kernel: no
  link followed, no automount triggered, and a call interrupted by a signal is tried again. Where
  the filesystem keeps no birth time (NFS, some FUSE mounts), ``statx`` says so in its mask and
  the value is absent, never invented. Where ``statx`` itself is missing (an older C library, a
  sandbox that refuses the call), the walk falls back to ``lstat`` and states no birth time.
- File numbers: Windows' directory listing gives no file number and no device (``DirEntry.stat``
  sets both to zero, which the census stores as none). On Windows a move is therefore known by its
  birth time, never by its number.
- Case is a default only. Each root's own names decide it at ``init``.
- The change time is the creation time on Windows, and a change to the file's metadata elsewhere.
  A folder's listed times are a stale cache on Windows, so the walk calls ``stat`` on a folder
  again there.
- Links: a Windows junction is a link that ``lstat`` calls a folder, so :func:`is_junction` is
  asked beside ``S_ISLNK`` wherever a walk refuses to follow a link. A dive reaches each file it
  reads from its root one name at a time and never through a link or a junction
  (:func:`open_beneath`, :func:`stat_beneath`).

The module reads what each system gives once per process. ``gives(platform)`` answers for any
system, so the tests cover every system's facts on every system. The moves (``scan/moves.py``)
read none of this. They read what two censuses recorded, never the machine they run on, so every
conclusion can be made again from the Index.
"""

from __future__ import annotations

import ctypes
import errno
import io
import os
import stat as stat_module
import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, BinaryIO, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "STATX_BTIME",
    "Gives",
    "NotTheRecordedFileError",
    "StatxResult",
    "System",
    "gives",
    "is_junction",
    "lstat_entry",
    "lstat_path",
    "makedev",
    "open_beneath",
    "parse_statx",
    "stat_beneath",
    "system_of",
]


class System(StrEnum):
    """The operating systems whose differences a walk needs to know."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    OTHER = "other"


class BirthTime(StrEnum):
    """How a file's birth time is read on a system."""

    #: In ``stat`` itself (``st_birthtime``): macOS, the BSDs, Windows from Python 3.12.
    STAT = "stat"
    #: With ``statx``, where the filesystem keeps one: Linux.
    STATX = "statx"
    #: Not at all.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Gives:
    """What one system gives a walk."""

    system: System
    birth_time: BirthTime
    #: Whether a directory listing's ``stat`` carries the file's number and its device.
    file_numbers_in_listing: bool
    #: Whether ``st_ctime`` is the creation time (Windows) rather than a metadata change.
    ctime_is_creation: bool
    #: Whether a listing's times for a folder are a cache that may be stale (Windows).
    folder_listing_stale: bool
    #: The case a new root is likely to have: a default only, each root's names decide.
    case_insensitive_by_default: bool
    #: The kinds of link the system makes.
    links: tuple[str, ...]


_GIVES: dict[System, Gives] = {
    System.MACOS: Gives(
        system=System.MACOS,
        birth_time=BirthTime.STAT,
        file_numbers_in_listing=True,
        ctime_is_creation=False,
        folder_listing_stale=False,
        case_insensitive_by_default=True,
        links=("symbolic",),
    ),
    System.LINUX: Gives(
        system=System.LINUX,
        birth_time=BirthTime.STATX,
        file_numbers_in_listing=True,
        ctime_is_creation=False,
        folder_listing_stale=False,
        case_insensitive_by_default=False,
        links=("symbolic",),
    ),
    System.WINDOWS: Gives(
        system=System.WINDOWS,
        birth_time=BirthTime.STAT,
        file_numbers_in_listing=False,
        ctime_is_creation=True,
        folder_listing_stale=True,
        case_insensitive_by_default=True,
        links=("symbolic", "junction"),
    ),
    System.OTHER: Gives(
        system=System.OTHER,
        birth_time=BirthTime.STAT,
        file_numbers_in_listing=True,
        ctime_is_creation=False,
        folder_listing_stale=False,
        case_insensitive_by_default=False,
        links=("symbolic",),
    ),
}


def system_of(platform: str) -> System:
    """The system a ``sys.platform`` value names."""
    if platform == "darwin":
        return System.MACOS
    if platform.startswith("linux"):
        return System.LINUX
    # Cygwin's Python is POSIX (``os.name`` is "posix"), so it does not count as Windows here.
    if platform == "win32":
        return System.WINDOWS
    return System.OTHER


def gives(platform: str | None = None) -> Gives:
    """What a system gives a walk: this one, or the one a ``sys.platform`` value names."""
    return _GIVES[system_of(sys.platform if platform is None else platform)]


# -- statx ------------------------------------------------------------------------------------

#: ``statx``'s arguments (``linux/stat.h``, ``fcntl.h``).
AT_FDCWD = -100
AT_SYMLINK_NOFOLLOW = 0x100
#: Never trigger an automount. The kernel's own ``lstat`` always adds this flag, and so does
#: glibc's ``statx``-based ``fstatat``. Without it a bare ``statx`` mounts an autofs or systemd
#: automount point it is asked about, where the ``lstat`` it stands in for recorded the point and
#: mounted nothing.
AT_NO_AUTOMOUNT = 0x800
AT_STATX_SYNC_AS_STAT = 0x0000
STATX_BASIC_STATS = 0x07FF
STATX_BTIME = 0x0800


class _StatxTimestamp(ctypes.Structure):
    _fields_ = (
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    )


class _Statx(ctypes.Structure):
    """``struct statx`` as the kernel fills it: 256 bytes, the same on every architecture."""

    _fields_ = (
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        ("spare2", ctypes.c_uint64 * 14),
    )


def makedev(major: int, minor: int) -> int:
    """A device number from its two halves, as glibc composes ``st_dev`` on Linux."""
    return (
        ((major & 0x00000FFF) << 8)
        | ((major & 0xFFFFF000) << 32)
        | (minor & 0x000000FF)
        | ((minor & 0xFFFFFF00) << 12)
    )


def _ns(stamp: _StatxTimestamp) -> int:
    return int(stamp.tv_sec) * 1_000_000_000 + int(stamp.tv_nsec)


class StatxResult:
    """What ``statx`` said about one entry, read the way the census reads an ``os.stat_result``.

    The result carries the fields a ``stat`` carries. It carries ``st_birthtime_ns`` only where the
    filesystem keeps a birth time (``STATX_BTIME`` in the mask). Otherwise the value is absent,
    never a zero.
    """

    __slots__ = (
        "st_atime_ns",
        "st_birthtime_ns",
        "st_blksize",
        "st_blocks",
        "st_ctime_ns",
        "st_dev",
        "st_gid",
        "st_ino",
        "st_mode",
        "st_mtime_ns",
        "st_nlink",
        "st_rdev",
        "st_size",
        "st_uid",
    )

    def __init__(self, found: _Statx) -> None:
        self.st_mode = int(found.stx_mode)
        self.st_ino = int(found.stx_ino)
        self.st_dev = makedev(int(found.stx_dev_major), int(found.stx_dev_minor))
        self.st_rdev = makedev(int(found.stx_rdev_major), int(found.stx_rdev_minor))
        self.st_nlink = int(found.stx_nlink)
        self.st_uid = int(found.stx_uid)
        self.st_gid = int(found.stx_gid)
        self.st_size = int(found.stx_size)
        self.st_blocks = int(found.stx_blocks)
        self.st_blksize = int(found.stx_blksize)
        self.st_atime_ns = _ns(found.stx_atime)
        self.st_mtime_ns = _ns(found.stx_mtime)
        self.st_ctime_ns = _ns(found.stx_ctime)
        if found.stx_mask & STATX_BTIME:
            self.st_birthtime_ns = _ns(found.stx_btime)

    @property
    def st_mtime(self) -> float:
        return self.st_mtime_ns / 1_000_000_000

    @property
    def st_ctime(self) -> float:
        return self.st_ctime_ns / 1_000_000_000

    @property
    def st_atime(self) -> float:
        return self.st_atime_ns / 1_000_000_000

    def __getattr__(self, name: str) -> Any:
        # A slot never set (the birth time a filesystem does not keep) reads as absent.
        if name == "st_birthtime_ns":
            return None
        raise AttributeError(name)


def parse_statx(buffer: bytes) -> StatxResult:
    """A ``struct statx`` as the kernel wrote it, read (the parsing tested on every system)."""
    if len(buffer) < ctypes.sizeof(_Statx):
        message = f"a statx structure is {ctypes.sizeof(_Statx)} bytes, not {len(buffer)}"
        raise ValueError(message)
    return StatxResult(_Statx.from_buffer_copy(buffer[: ctypes.sizeof(_Statx)]))


class _StatxCall:
    """``statx`` from the C library, looked up once, or None where there is none to call."""

    def __init__(self) -> None:
        self.function: Any = None
        self.looked = False

    def get(self) -> Any:
        if not self.looked:
            self.looked = True
            if system_of(sys.platform) is System.LINUX:
                try:
                    library = ctypes.CDLL(None, use_errno=True)
                    function = library.statx
                except (OSError, AttributeError):
                    function = None
                if function is not None:
                    function.argtypes = (
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_uint,
                        ctypes.POINTER(_Statx),
                    )
                    function.restype = ctypes.c_int
                self.function = function
        return self.function


_CALL = _StatxCall()

#: ``statx`` missing from the kernel: the walk uses ``lstat`` for the rest of the process.
_MISSING = errno.ENOSYS
#: ``statx`` refused by a sandbox (an older container's filter answers so): ``lstat`` answers
#: this call, and gives the path's own error where there is one.
_REFUSED = errno.EPERM


def _statx(path: str) -> StatxResult | None:
    """``statx`` of one path without following a link, or None where the call is unavailable."""
    function = _CALL.get()
    if function is None:
        return None
    found = _Statx()
    spelled = os.fsencode(path)
    while True:
        code = function(
            AT_FDCWD,
            spelled,
            AT_SYMLINK_NOFOLLOW | AT_NO_AUTOMOUNT | AT_STATX_SYNC_AS_STAT,
            STATX_BASIC_STATS | STATX_BTIME,
            ctypes.byref(found),
        )
        # A signal during the call: tried again, as Python's own stat does (PEP 475).
        if code == 0 or ctypes.get_errno() != errno.EINTR:
            break
    if code != 0:
        number = ctypes.get_errno()
        if number == _MISSING:
            _CALL.function = None
            return None
        if number == _REFUSED:
            return None
        raise OSError(number, os.strerror(number), path)
    return StatxResult(found)


def lstat_entry(item: os.DirEntry[str]) -> os.stat_result:
    """The one ``stat`` a walk makes of a listed entry, without following a link: ``statx`` on
    Linux, for the birth time, and the listing's own ``stat`` everywhere else. A
    :class:`StatxResult` carries every field the census reads of a ``stat_result``."""
    read = _statx(item.path)
    return cast("os.stat_result", read) if read is not None else item.stat(follow_symlinks=False)


def lstat_path(path: Path) -> os.stat_result:
    """The same for a path the walk was given (a root), not one it listed."""
    read = _statx(os.fspath(path))
    return cast("os.stat_result", read) if read is not None else path.lstat()


# -- links, junctions, and reaching a file beneath its root -------------------------------------

#: Whether this system makes junctions. Only Windows does. Everywhere else :func:`is_junction`
#: answers no without a call, so a walk there is exactly what it was before junctions were asked.
_MAKES_JUNCTIONS = "junction" in gives().links


def is_junction(item: str | os.PathLike[str] | os.DirEntry[str]) -> bool:
    """Whether a listed entry or a path is a Windows junction, asked without following it.

    A junction is a link. It names a folder somewhere else, often on another drive, and the
    system walks through it as it walks through a symbolic link. Yet ``lstat`` reports it as the
    folder it names (``S_ISDIR``, not ``S_ISLNK``), and ``DirEntry.is_symlink`` says no. A walk
    that asked only those two would take a junction for a folder of the root and walk out of the
    root through it, so every place a walk refuses a link asks this too. A listed entry is asked
    through its listing (``DirEntry.is_junction``, which costs no call, because a Windows listing
    already carries the reparse tag), and a path through ``os.path.isjunction`` (one ``lstat``).
    """
    if not _MAKES_JUNCTIONS:
        return False
    if isinstance(item, os.DirEntry):
        return item.is_junction()
    return os.path.isjunction(item)


class NotTheRecordedFileError(OSError):
    """A file refused rather than opened: what its path reaches now is not the file recorded there.

    There are three reasons. The way to it from its root passes through a link or a junction.
    What is there is not a regular file. Or its inode or device is not the one recorded. Each is a
    fact about the file, and none is a fault of the reading. The class is an ``OSError`` so that
    every reader that already takes a file it cannot open as unreadable takes this one so too,
    and reads nothing of it.
    """


#: Whether a walk can reach a file one folder at a time by descriptor: each folder opened inside
#: the one before it with ``O_NOFOLLOW``, so no change to the tree while the walk runs can turn
#: it through a link. Every POSIX system the engine runs on can. Windows has neither
#: ``O_NOFOLLOW`` nor ``openat``, and walks by ``lstat`` instead (:func:`open_beneath`).
_WALKS_BY_DESCRIPTOR = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
)
#: How every name on the way is opened: for reading only, and never inherited by a child.
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
#: How the file itself is opened besides. A link in its place is refused. A pipe put in its place
#: is never waited on, where a plain opening would block until something wrote to it. A terminal
#: is never made the engine's own. None of the three changes how a regular file reads.
_FILE_FLAGS = (
    getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
)
#: The 64 bits a device or inode number is compared on. The Index keeps a device as the signed
#: reading of its bits (``census.device_as_stored``) and a system may hand it over unsigned, so
#: two readings of one number agree only when their bits are compared.
_BITS = (1 << 64) - 1


def _names(path: Path, root: Path) -> tuple[str, ...]:
    """The names that lead from ``root`` to ``path``, read from their spelling alone."""
    names = path.relative_to(root).parts
    if os.pardir in names:
        message = f"{path} is not spelled as a path beneath {root}"
        raise ValueError(message)
    return names


def _through(spelled: str, kind: str, *, last: bool) -> str:
    """Say in words where a walk met a link or a junction."""
    if last:
        return f"it is now a {kind} ({spelled}), and nothing a {kind} leads to is read"
    return (
        f"the way to it passes through a {kind} at {spelled}, and nothing a {kind} leads to is read"
    )


def _walk_by_lstat(root: Path, names: Sequence[str]) -> tuple[str, os.stat_result]:
    """The path of a regular file beneath ``root``, and its ``lstat``, each name looked at in turn.

    Each name on the way is looked at with ``lstat``, never ``stat``, so nothing a link leads to
    is looked at. A link or a junction is refused where it is met, and no name after it is ever
    spelled. A file where a folder should be raises what the system would say of it,
    ``NotADirectoryError``, because the path is then broken rather than turned elsewhere.
    """
    step = os.fspath(root)
    found = os.lstat(step) if not names else None
    for position, name in enumerate(names):
        step = os.path.join(step, name)
        found = os.lstat(step)
        spelled = "/".join(names[: position + 1])
        last = position == len(names) - 1
        if stat_module.S_ISLNK(found.st_mode):
            raise NotTheRecordedFileError(_through(spelled, "link", last=last))
        if stat_module.S_ISDIR(found.st_mode) and is_junction(step):
            raise NotTheRecordedFileError(_through(spelled, "junction", last=last))
        if not last and not stat_module.S_ISDIR(found.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), step)
    if found is None or not stat_module.S_ISREG(found.st_mode):
        raise NotTheRecordedFileError("what is there now is not a regular file")
    return step, found


def stat_beneath(path: Path, root: Path) -> os.stat_result:
    """The ``lstat`` of a regular file, reached from ``root`` one name at a time, no link followed.

    Each folder on the way is looked at with ``lstat`` and refused where it is a link or a
    junction, so nothing a link leads to is looked at, and the file itself must be a regular
    file. A refusal is :class:`NotTheRecordedFileError`. A name that is gone or that cannot be
    looked at raises what ``lstat`` raised. The root is taken as it was declared, links in its
    own spelling and all, because the root is the person's choice of folder.

    The answer is a reading of metadata. It promises nothing about a later opening, because the
    tree may change in between. :func:`open_beneath` is what guards the bytes.
    """
    _, found = _walk_by_lstat(root, _names(path, root))
    return found


def _is_link_at(folder: int | None, name: str) -> bool:
    """Whether a name inside an open folder (or a path, with no folder) is a link, unfollowed."""
    with suppress(OSError):
        return stat_module.S_ISLNK(os.stat(name, dir_fd=folder, follow_symlinks=False).st_mode)
    return False


def _open_at(folder: int | None, name: str, flags: int, spelled: str, *, last: bool) -> int:
    """One name opened inside an open folder with ``O_NOFOLLOW``, and a link there refused.

    The systems answer a link under ``O_NOFOLLOW`` differently: ``ELOOP`` on Linux, ``ENOTDIR``
    on macOS for a folder, ``EMLINK`` on FreeBSD. So any failure is followed by one unfollowed
    look at the name, which says whether a link was the reason.
    """
    try:
        return os.open(name, flags, dir_fd=folder)
    except OSError as exc:
        if _is_link_at(folder, name):
            raise NotTheRecordedFileError(_through(spelled, "link", last=last)) from exc
        raise


def _open_by_descriptor(root: str, names: Sequence[str]) -> int:
    """The file at ``names`` beneath ``root``, each folder opened inside the one before it."""
    if not names:
        # The root is itself the file. It is taken as declared, but a link in its place is still
        # refused: the census records such a root as a link, never as a file.
        return _open_at(None, root, _OPEN_FLAGS | _FILE_FLAGS, root, last=True)
    folder = os.open(root, _OPEN_FLAGS | os.O_DIRECTORY)
    try:
        for position, name in enumerate(names[:-1]):
            spelled = "/".join(names[: position + 1])
            inner = _open_at(
                folder, name, _OPEN_FLAGS | os.O_NOFOLLOW | os.O_DIRECTORY, spelled, last=False
            )
            os.close(folder)
            folder = inner
        return _open_at(folder, names[-1], _OPEN_FLAGS | _FILE_FLAGS, "/".join(names), last=True)
    finally:
        os.close(folder)


def _refuse_unrecorded(opened: os.stat_result, device: int | None, inode: int | None) -> None:
    """Refuse an opened file that is not a regular file, or that is not the one recorded."""
    if not stat_module.S_ISREG(opened.st_mode):
        raise NotTheRecordedFileError("what is there now is not a regular file")
    for what, recorded, now in (("inode", inode, opened.st_ino), ("device", device, opened.st_dev)):
        if recorded is not None and (int(recorded) & _BITS) != (int(now) & _BITS):
            raise NotTheRecordedFileError(
                f"its {what} is {int(now)}, where {recorded} was recorded"
            )


def open_beneath(
    path: Path, root: Path, *, device: int | None = None, inode: int | None = None
) -> BinaryIO:
    """Open a regular file for reading, reached from ``root`` one name at a time, no link followed.

    The root is opened as it was declared, links in its own spelling and all, because the root is
    the person's choice of folder. Nothing beneath it is followed. A link or a junction where a
    folder was, or in place of the file, is refused before anything past it is opened, and so is
    anything that is not a regular file, such as a folder, a pipe or a device. A ``device`` or an
    ``inode`` given is the one recorded for the file, and the opened file must carry it: it is
    read with ``fstat`` after the opening and before any byte. Each refusal is
    :class:`NotTheRecordedFileError`, raised with the file already closed, so nothing of it is
    read. A name that is gone or that cannot be opened raises what the system raised.

    On POSIX the walk is made by descriptor. Each folder is opened inside the one before it with
    ``O_NOFOLLOW``, so a folder swapped for a link while the walk runs is refused as well. On
    Windows, which has neither ``O_NOFOLLOW`` nor ``openat``, each name is looked at with
    ``lstat`` and asked :func:`is_junction`, the file is opened by its path, and the file opened
    must be the one looked at, by its volume and file number. A swap between the look and the
    opening is then refused before a byte is read, though the opening has by then reached
    whatever the swap put there. That is as far as Windows goes without a handle per folder.
    """
    names = _names(path, root)
    looked: os.stat_result | None = None
    if _WALKS_BY_DESCRIPTOR:
        descriptor = _open_by_descriptor(os.fspath(root), names)
    else:
        spelled, looked = _walk_by_lstat(root, names)
        descriptor = os.open(spelled, _OPEN_FLAGS | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(descriptor)
        if looked is not None and (looked.st_dev, looked.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise NotTheRecordedFileError(
                "the file opened is not the one looked at a moment before: the way to it "
                "changed while it was reached"
            )
        _refuse_unrecorded(opened, device, inode)
        raw = io.FileIO(descriptor, "rb", closefd=True)
    except BaseException:
        os.close(descriptor)
        raise
    # Named by its path, as ``Path.open`` names a file, and not by its descriptor's number, so a
    # reader that says which file it read says it in words.
    raw.name = os.fspath(path)
    blocks = int(getattr(opened, "st_blksize", 0) or 0)
    return io.BufferedReader(raw, buffer_size=blocks if blocks > 1 else io.DEFAULT_BUFFER_SIZE)
