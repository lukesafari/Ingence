"""Revision 6, names: when a label nobody has taught the engine is asked about.

- ``indexed_label_may_mark_replicates`` v2: a label the Dictionary places nowhere, numbered from 1
  without a gap, is asked about where the run reaches up to ``maximum_index`` 24, and no longer
  only up to 9. The invented laboratory marks replicates ``R1`` to ``R11``: a dozen dishes of one
  condition, or a plate's row of twelve, is an ordinary replicate set, and version 1 read every run
  past 9 as a version and never asked. Nothing is decided: the engine still only notices the shape
  and asks, and a person's answer is the Dictionary edit after which every rule reading replicate
  senses reads the label.
"""

from __future__ import annotations

from ingence.rulebook.revision_6.earlier import declared_in_revision_4, parameter_of

__all__ = ["CHANGES", "RULES"]


def _label_question() -> dict[str, object]:
    rule = declared_in_revision_4("indexed_label_may_mark_replicates")
    rule["version"] = "2"
    maximum = parameter_of(rule, "maximum_index")
    maximum["default"] = 24
    maximum["description"] = (
        "How far a run from one may reach and still look like a replicate set. A dozen dishes "
        "of one condition, or a plate's row of twelve, is one; a version or a frame series runs "
        "on past a set's size."
    )
    rule["rationale"] = (
        f"{rule['rationale']} Version 2 asks about a run up to 24. A laboratory marking eleven "
        "replicates R1 to R11 was never asked, because version 1 read any run past 9 as a version. "
        "A run that is a version costs one question answered no."
    )
    return rule


#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [_label_question()]

#: What this module changes in rules an earlier revision declared: nothing. A rule declared again
#: at a higher version displaces the earlier one by its version.
CHANGES: list[dict[str, object]] = []
