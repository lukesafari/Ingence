"""``ingence export brief`` replaces each of its three files and never writes through a link.

A link standing at ``brief.md``, ``brief.json`` or ``brief.aliases.json`` (in a folder a person
named with ``--overwrite``, or under the project's exports, where the next export replaces the last
without asking) once had the brief written into whatever it led to, a file in a data folder
included. Each file is now written beside itself and moved into place, so the link is replaced and
the file it led to keeps its bytes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ingence.agent.brief import BRIEF_FILES, write_brief
from ingence.agent.shape import Aliases

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("name", BRIEF_FILES)
def test_a_link_at_a_brief_file_is_replaced_and_what_it_led_to_is_untouched(
    tmp_path: Path, name: str
) -> None:
    data = tmp_path / "pool" / "exp1"
    data.mkdir(parents=True)
    precious = data / "precious.txt"
    precious.write_text("a person's measurements\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    try:
        (out / name).symlink_to(precious)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    brief = SimpleNamespace(markdown="# A brief\n", json_text="{}\n", aliases=Aliases())
    written = write_brief(brief, out)  # type: ignore[arg-type]
    assert precious.read_text(encoding="utf-8") == "a person's measurements\n"
    assert not (out / name).is_symlink()
    assert [path.name for path in written] == list(BRIEF_FILES)
    assert (out / "brief.md").read_text(encoding="utf-8") == "# A brief\n"
