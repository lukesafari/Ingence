"""Revision 6, derivations: which of a script's inputs made which of its outputs.

- ``derivation_by_a_scripts_calls`` v2: each file a script writes is made from the files it read in
  the same step of its calls, after its previous write (``pairs_within``). Version 1 paired every
  file a script reads with every file it writes. On the invented laboratory's middle collection
  that was 4,239 wrong derivations beside 737 right ones, and reading by step kept every right one.
"""

from __future__ import annotations

from ingence.rulebook.revision_6.earlier import declared_in_revision_4

__all__ = ["CHANGES", "RULES"]


def _parameters(rule: dict[str, object]) -> list[object]:
    """The parameters an earlier declaration lists, to declare again with more beside them."""
    declared = rule["parameters"]
    return list(declared) if isinstance(declared, list) else []


def _scripts_calls() -> dict[str, object]:
    rule = declared_in_revision_4("derivation_by_a_scripts_calls")
    rule["version"] = "2"
    rule["statement"] = (
        "A file is proposed as processed from another where one script of type "
        "{describing_formats} reads the other and writes it, by literal names its calls state, "
        "each resolved at {minimum_resolution} or better within the script's own folder or "
        "experiment, the pair read within {pairs_within} (one step being what the script reads "
        "after its previous write and the files it writes next), and the pair is blocked where "
        "either lies in a folder laid out as an installed program."
    )
    rule["rationale"] = (
        f"{rule['rationale']} Version 2 reads the calls in order. A script that reads one table, "
        "writes its plot, then reads the next table and writes that plot made each plot from its "
        "own table. Read as two lists, every plot was made from every table: on the invented "
        "laboratory's middle collection, 4,239 wrong derivations beside 737 right ones, and "
        "reading by step kept all 737."
    )
    rule["parameters"] = [
        *_parameters(rule),
        {
            "name": "pairs_within",
            "type": "enum",
            "default": "one_step",
            "choices": ["whole_script", "one_step"],
            "description": (
                "Which reads a written file is made from: those of its own step (one_step), or "
                "every read in the script (whole_script, as version 1 read it). A script read "
                "before its calls' order was recorded is read whole."
            ),
        },
    ]
    return rule


#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [_scripts_calls()]

#: What this module changes in rules an earlier revision declared: nothing. A rule declared again
#: at a higher version displaces the earlier one by its version.
CHANGES: list[dict[str, object]] = []
