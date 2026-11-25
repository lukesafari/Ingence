"""The earlier declarations revision 6 declares again, read from where they were declared.

A rule that revision 6 declares again at a higher version keeps every part of its earlier
declaration that does not change. Copying the parts by hand would give two places to edit one rule
in, so each module here starts from a copy of the earlier declaration and states what changes.
Nothing earlier is edited: the copy is deep, and the earlier revision's dict is left as it was.
"""

from __future__ import annotations

import copy

__all__ = ["declared_in_revision_4", "parameter_of"]


def declared_in_revision_4(rule_id: str) -> dict[str, object]:
    """A deep copy of revision 4's one declaration of a rule, refused where it makes none."""
    from ingence.rulebook.revision_4 import REVISION

    found = [
        rule
        for rule in REVISION["rules"]  # type: ignore[attr-defined]
        if isinstance(rule, dict) and rule.get("id") == rule_id
    ]
    if len(found) != 1:
        message = f"revision 4 declares {rule_id!r} {len(found)} times, and revision 6 reads one"
        raise ValueError(message)
    return copy.deepcopy(found[0])


def parameter_of(rule: dict[str, object], name: str) -> dict[str, object]:
    """The declaration of one of a rule's parameters, to change in place on a copy."""
    for parameter in rule["parameters"]:  # type: ignore[attr-defined]
        if parameter["name"] == name:
            return parameter  # type: ignore[no-any-return]
    message = f"{rule['id']!r} declares no parameter {name!r}"
    raise ValueError(message)
