"""The oldest dependencies the package allows are the ones the minimum install is tested with.

``pyproject.toml`` states a lower bound for each dependency, and a person whose installer picks
exactly that version must get a working engine. DuckDB 1.0 met the bound and stopped every census
at its first compaction. Pydantic 2.7 met it and published no claim from the extension rules, with
no error. ``tools/minimum-constraints.txt`` pins every bound so that install can be built and run on
purpose (the ``minimum`` job in ``.github/workflows/checks.yml``). This test holds the two files to
each other: a floor raised or added in one and not the other fails here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The extras the minimum job installs with the base.
EXTRAS = ("content",)


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _floors() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    stated = [*project["dependencies"]]
    for extra in EXTRAS:
        stated += project["optional-dependencies"][extra]
    floors: dict[str, str] = {}
    for requirement in stated:
        found = re.fullmatch(r"\s*([A-Za-z0-9_.\-]+)\s*>=\s*([^,;\s]+).*", requirement)
        assert found, f"{requirement!r} states no lower bound"
        floors[_key(found.group(1))] = found.group(2)
    return floors


def _pinned() -> dict[str, str]:
    pinned: dict[str, str] = {}
    for line in (ROOT / "tools" / "minimum-constraints.txt").read_text("utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        name, _, version = text.partition("==")
        assert version, f"{text!r} in the minimum constraints is not pinned with =="
        pinned[_key(name)] = version.strip()
    return pinned


def _same(left: str, right: str) -> bool:
    """Whether two versions name one release, ``1.0`` and ``1.0.0`` alike."""

    def parts(version: str) -> list[str]:
        pieces = version.split(".")
        while len(pieces) > 1 and pieces[-1] == "0":
            pieces.pop()
        return pieces

    return parts(left) == parts(right)


def test_every_lower_bound_is_pinned_at_exactly_that_version() -> None:
    floors, pinned = _floors(), _pinned()
    assert set(floors) == set(pinned)
    different = {
        name: (floors[name], pinned[name])
        for name in floors
        if not _same(floors[name], pinned[name])
    }
    assert different == {}
