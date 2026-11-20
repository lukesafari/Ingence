"""An image's picture fingerprint never hands its bytes to another program.

Pillow chooses a decoder from a file's bytes, whatever its name says, and its EPS decoder runs
Ghostscript on the file. A ``figure.png`` that holds PostScript would therefore be run as a program
by hashing it. The fingerprint lets Pillow try only the raster formats it is for, so such a file
gets no fingerprint and nothing runs. The ``images`` extra (imagehash) may be missing, so a
stand-in with the same call is used; Ghostscript is a stand-in on ``PATH`` that records being run.
"""

from __future__ import annotations

import io
import os
import sys
import types
from typing import TYPE_CHECKING, Any

import pytest

from ingence.extractors.base import ExtractionContext

if TYPE_CHECKING:
    from pathlib import Path

PIL = pytest.importorskip("PIL.Image")

POSTSCRIPT = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 8 8\nshowpage\n"


@pytest.fixture
def hashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    stand_in = types.ModuleType("imagehash")
    stand_in.dhash = lambda image, hash_size: f"{image.format}:{image.size}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "imagehash", stand_in)
    tools = tmp_path / "bin"
    tools.mkdir()
    ran = tmp_path / "ghostscript-ran"
    for name in ("gs", "gswin64c", "gswin32c"):
        program = tools / name
        program.write_text(f"#!/bin/sh\necho ran > '{ran}'\n", encoding="utf-8")
        program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    from ingence.identity.perceptual import ImagePerceptualHash

    return ImagePerceptualHash(), ran


def _context(path: Path) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({path.suffix}),
        size_bytes=path.stat().st_size,
        media_type=None,
        limits=None,
    )


def test_postscript_named_as_a_picture_gets_no_fingerprint_and_nothing_runs(
    tmp_path: Path, hashing: Any
) -> None:
    method, ran = hashing
    path = tmp_path / "figure.png"
    path.write_bytes(POSTSCRIPT)
    assert method.compute(_context(path)) is None
    assert not ran.exists()


def test_a_real_picture_is_still_fingerprinted(tmp_path: Path, hashing: Any) -> None:
    method, _ = hashing
    path = tmp_path / "plate.png"
    buffer = io.BytesIO()
    PIL.new("RGB", (8, 8)).save(buffer, "PNG")
    path.write_bytes(buffer.getvalue())
    result = method.compute(_context(path))
    assert result is not None and result.value.startswith("PNG")
