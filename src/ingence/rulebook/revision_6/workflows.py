"""Revision 6, stages: what a folder's name states about the files in it, and an instrument's
metadata beside its acquisition.

- ``stage_inherited_from_folder`` v2: a file whose own name states no stage takes the one stage
  its enclosing folder's name states (``proc/``, ``Raw Data/``, ``results/``), one hop up and
  never further. Version 1 was declared and never evaluable: nothing assembled the folder's records
  beside the file's. On the invented laboratory's middle collection, 1,884 files had no stage; this
  rule staged 1,177 of them and none wrongly.
- ``metadata_stage_from_its_acquisition`` v1 (new): an instrument's metadata file, named as the
  acquisition beside it but for the extension (``A549_001.xml`` beside ``A549_001.nd2``), is raw
  data. It was written as the data was acquired. It is not itself an acquisition: a file concluded
  at the acquisition stage is one other files are named for, and two of one name beside each other
  would leave every analysis of the movie named for neither.
"""

from __future__ import annotations

from ingence.rulebook.revision_5.workflows import _BUNDLE_FORMATS, _PIPELINE_STAGES

__all__ = ["CHANGES", "RULES"]

_QUALITY = {"type": "enum", "choices": ["exact", "case_folded", "loose"]}

#: The shipped noise and placeholder defeaters, as revision 4 names them on every grouping.
_NOT_NOISE_NOR_PLACEHOLDER = ["filesystem_noise_from_spelling", "placeholder_from_entry_kind"]

#: The rules that say a file's stage from the file itself. Where one concludes, the file says its
#: own stage and its folder's is not taken.
_OWN_STAGE_RULES = [
    "acquisition_stage_from_format",
    "analysis_stage_from_derived_sense",
    "export_stage_from_inner_format",
    "metadata_stage_from_its_acquisition",
    "pipeline_stage_from_name",
    "stage_from_writer_tags",
]

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [
    {
        "id": "stage_inherited_from_folder",
        "version": "2",
        "predicate": "stage_from_enclosing_folder",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": [
            *_OWN_STAGE_RULES,
            "program_from_extension",
            "installed_software_in_folder",
            *_NOT_NOISE_NOR_PLACEHOLDER,
        ],
        "statement": (
            "A file whose own name states none of {senses} takes the one sense of kind "
            "{theme_kind} among them that its enclosing folder's name states, as a whole part of "
            "the name, matched at quality {minimum_match_quality} or better, across at most "
            "{maximum_hops} level of containment, never where its extension is one of "
            "{not_in_formats}, unless the file's own name, format, content or derivation states "
            "its stage, it is a program, filesystem noise or a placeholder, or it lies in a folder "
            "laid out as an installed program."
        ),
        "rationale": (
            "People put the distinguishing part in the filename and the shared part in the folder "
            "above it, so inheriting one hop is reading the convention rather than guessing; the "
            "bound is hard because containment is transitive while the inference is not, and a "
            "walk two levels up hands an attribute to branches nobody was looking at. Version 1 "
            "could not be evaluated, because nothing handed the folder's name beside the file's. "
            "A folder whose name states two stages states none, and a file whose own name, "
            "format or content states a stage keeps its own."
        ),
        "parameters": [
            {
                "name": "theme_kind",
                "type": "string",
                "default": "stage",
                "description": "The axis inherited.",
            },
            {
                "name": "senses",
                "type": "string_list",
                "default": _PIPELINE_STAGES,
                "description": (
                    "The pipeline stages, as the name rule reads them. The transformations done "
                    "at a stage, such as cropped or merged, are not stages a folder hands down."
                ),
            },
            {
                "name": "minimum_match_quality",
                **_QUALITY,
                "default": "case_folded",
                "description": "How firmly the folder's stage word must have matched.",
            },
            {
                "name": "maximum_hops",
                "type": "integer",
                "default": 1,
                "minimum": 1,
                "maximum": 1,
                "description": (
                    "How far up the inheritance reaches. Bounded at one and bounded in the "
                    "declaration, because a tunable depth is an invitation to set it to three."
                ),
            },
            {
                "name": "not_in_formats",
                "type": "string_list",
                "default": _BUNDLE_FORMATS,
                "description": "A file of one of these formats is a bundle and is not staged.",
            },
        ],
    },
    {
        "id": "metadata_stage_from_its_acquisition",
        "version": "1",
        "predicate": "stage_beside_its_acquisition",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": [
            "content_contradicts_extension",
            "pipeline_stage_from_name",
            "installed_software_in_folder",
            *_NOT_NOISE_NOR_PLACEHOLDER,
        ],
        "statement": (
            "A file whose extension matches one of {formats} at quality {minimum_match_quality} "
            "or better, and whose name is, but for its extension, that of a file beside it in a "
            "spelling an instrument writes, sits at the {concludes} stage, unless its bytes "
            "contradict its extension, its own name states a stage, it is filesystem noise or a "
            "placeholder, or it lies in a folder laid out as an installed program."
        ),
        "rationale": (
            "An instrument writes its metadata beside each acquisition under the acquisition's "
            "own name, as the data is acquired, so the file is raw data and not something made "
            "from it later. An analysis tool names what it writes for the movie it read with "
            "words of its own beside the movie's name, and such a name is not the movie's whole. "
            "It is raw and not an acquisition, so the movie stays the one file its analyses are "
            "named for."
        ),
        "parameters": [
            {
                "name": "formats",
                "type": "string_list",
                "default": ["xml_document"],
                "description": "The formats an instrument writes its metadata in, as senses.",
            },
            {
                "name": "minimum_match_quality",
                **_QUALITY,
                "default": "exact",
                "description": "How firmly the extension must have matched: it is a symbol.",
            },
            {
                "name": "concludes",
                "type": "string",
                "default": "raw",
                "description": (
                    "The stage concluded: raw, and never acquisition, which would make the "
                    "metadata a second movie of the same name for the analysis to be named for."
                ),
            },
        ],
    },
]

#: What this module changes in rules an earlier revision declared: nothing. A rule declared again
#: at a higher version displaces the earlier one by its version.
CHANGES: list[dict[str, object]] = []
