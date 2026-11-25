"""Revision 6 of the shipped Rulebook: the rules an accuracy test on the invented laboratory asked
for.

The invented laboratory's three collections, scored against their answer keys (the 0.4.0 report's
supplements), showed where the 0.4.0 rules go wrong, and each module here declares again the rules
that test measured, or adds one. Rules supersede earlier revisions and never edit them, as revision
4's header says. A rule whose reading changes is declared again at a higher version of its own
identifier, which displaces the earlier declaration (``rulebook/resolve.py``: the later version in
force), so the superseded sentence stays readable and every past run still replays.

The revision is a package with one module for each topic. Each module exports ``RULES`` and
``CHANGES``, assembled here into one dict shaped exactly like
:data:`ingence.rulebook.revision_4.REVISION`:

- :mod:`~ingence.rulebook.revision_6.workflows`: the stage a folder's name states, and an
  instrument's metadata beside its acquisition;
- :mod:`~ingence.rulebook.revision_6.derivations`: a script's inputs and outputs read step by
  step;
- :mod:`~ingence.rulebook.revision_6.experiments`: an instrument's metadata in an experiment
  folder's count.

:func:`ingence.rulebook.load.load_revisions` loads the revision after revision 5.
"""

from __future__ import annotations

from ingence.rulebook.revision_6 import derivations, experiments, workflows

__all__ = ["REVISION"]

#: The modules of the revision, in the order their rules and changes are declared.
_TRACKS = (workflows, derivations, experiments)

REVISION: dict[str, object] = {
    "revision": 6,
    "summary": (
        "The rules the invented laboratory's accuracy test asked for: a folder's stage, an "
        "instrument's metadata, and a script's calls read step by step. Supersedes what it "
        "changes and edits nothing earlier."
    ),
    "changes": [change for track in _TRACKS for change in track.CHANGES],
    "rules": [rule for track in _TRACKS for rule in track.RULES],
}
