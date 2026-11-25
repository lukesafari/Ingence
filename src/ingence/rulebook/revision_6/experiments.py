"""Revision 6, experiments: where one experiment's material is bound together, and which words
say which experiments belong together.

- ``experiment_folder_from_contents`` v4: an instrument's metadata beside an acquisition, named
  as the acquisition but for the extension (``A549_001.xml`` beside ``A549_001.nd2``), is part of
  that acquisition and not a file named for it (``sidecar_formats``). Version 3 read each such
  file as an analysis of its movie, so a folder of movies and their metadata held "acquisitions and
  files named for them" and was called an experiment: all 36 folders it wrongly called one on the
  invented laboratory's middle collection were such folders.
- ``group_resolved_across_experiments`` v4: the related-experiments questions are built from the
  words that say what an experiment is about. A word matched to a condition, a technique, an
  acquisition setting, a reagent, a file's role, a channel or an instrument is not a term, as
  stage and replicate words were not (``ignored_theme_kinds``). Experiments sharing a technique or
  a treatment are not one project's: on the middle collection, 8,408 of the 9,398 pairs the
  questions proposed were wrong, most of them groups named by such a word.
"""

from __future__ import annotations

from ingence.rulebook.revision_6.earlier import declared_in_revision_4, parameter_of

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


#: The kinds whose words say how, with what or under which condition an experiment was done, and
#: never what it is about. Stage, replicate and connecting words were left out before.
_HOW_AN_EXPERIMENT_WAS_DONE = [
    "stage",
    "replicate",
    "connective",
    "condition",
    "technique",
    "acquisition",
    "reagent",
    "file_role",
    "channel",
    "instrument",
]


def _resolved_across_experiments() -> dict[str, object]:
    rule = declared_in_revision_4("group_resolved_across_experiments")
    rule["version"] = "4"
    ignored = parameter_of(rule, "ignored_theme_kinds")
    ignored["default"] = _HOW_AN_EXPERIMENT_WAS_DONE
    ignored["description"] = (
        "A word matched to a sense of these kinds is not a term: it says how, with what or under "
        "which condition an experiment was done, and not what it is about."
    )
    rule["rationale"] = (
        f"{rule['rationale']} Version 4 leaves out the words that say how an experiment was done. "
        "Two experiments imaged by one technique, or given one drug, are not one project's, and "
        "a question naming such a word joined experiments nobody would put together: on the "
        "invented laboratory's middle collection, about nine in ten of the pairs the questions "
        "proposed. It asks about fewer groups, and so finds fewer of the right ones."
    )
    return rule


#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [_experiment_from_contents(), _resolved_across_experiments()]

#: What this module changes in rules an earlier revision declared: nothing. A rule declared again
#: at a higher version displaces the earlier one by its version.
CHANGES: list[dict[str, object]] = []
