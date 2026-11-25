"""Revision 6, experiments: where one experiment's material is bound together.

- ``experiment_folder_from_contents`` v4: an instrument's metadata beside an acquisition, named
  as the acquisition but for the extension (``A549_001.xml`` beside ``A549_001.nd2``), is part of
  that acquisition and not a file named for it (``sidecar_formats``). Version 3 read each such
  file as an analysis of its movie, so a folder of movies and their metadata held "acquisitions and
  files named for them" and was called an experiment: all 36 folders it wrongly called one on the
  invented laboratory's middle collection were such folders.
"""

from __future__ import annotations

from ingence.rulebook.revision_6.earlier import declared_in_revision_4

__all__ = ["CHANGES", "RULES"]


def _parameters(rule: dict[str, object]) -> list[object]:
    """The parameters an earlier declaration lists, to declare again with more beside them."""
    declared = rule["parameters"]
    return list(declared) if isinstance(declared, list) else []


def _experiment_from_contents() -> dict[str, object]:
    rule = declared_in_revision_4("experiment_folder_from_contents")
    rule["version"] = "4"
    statement = str(rule["statement"])
    held = "are an instrument's acquisitions or are named for one"
    if held not in statement:
        message = "experiment_folder_from_contents v3 no longer says what its files are"
        raise ValueError(message)
    rule["statement"] = statement.replace(
        held,
        f"{held} (a file of format {{sidecar_formats}} whose name is an acquisition's beside it, "
        "but for its extension, being part of that acquisition and named for nothing)",
        1,
    )
    rule["rationale"] = (
        f"{rule['rationale']} Version 4 reads an instrument's metadata beside its acquisition as "
        "part of it. A folder of movies and their metadata files holds acquisitions only, and "
        "version 3 called every such folder an experiment."
    )
    rule["parameters"] = [
        *_parameters(rule),
        {
            "name": "sidecar_formats",
            "type": "string_list",
            "default": ["xml_document"],
            "description": (
                "The formats an instrument writes its metadata in beside each acquisition, as "
                "senses."
            ),
        },
    ]
    return rule


#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [_experiment_from_contents()]

#: What this module changes in rules an earlier revision declared: nothing. A rule declared again
#: at a higher version displaces the earlier one by its version.
CHANGES: list[dict[str, object]] = []
