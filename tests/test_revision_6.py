"""Revision 6 ships the rules the invented laboratory's accuracy test asked for: it loads, numbers
itself 6, composes after revision 5, and each rule it declares again keeps its earlier version
readable. Every name is invented.
"""

from __future__ import annotations

from ingence.rulebook import shipped_rulebook
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.resolve import effective_rulebook
from ingence.rulebook.revision_6 import REVISION


def test_revision_6_loads_and_numbers_itself_6() -> None:
    assert REVISION["revision"] == 6
    assert set(REVISION) == {"revision", "summary", "rules", "changes"}
    load_builtins()
    revisions = load_revisions()
    numbers = [revision.number for revision in revisions]
    assert numbers[numbers.index(5) : numbers.index(5) + 2] == [5, 6]
    sixth = revisions[numbers.index(6)]
    assert sixth.summary
    assert {rule.rule_id for rule in sixth.rules} == {
        rule["id"]
        for rule in REVISION["rules"]  # type: ignore[union-attr]
    }


def test_revision_6_declares_each_rule_again_at_a_higher_version() -> None:
    """A rule revision 6 declares again is in force at its new version, and the book at revision
    5 still holds the version it had, so a run made before replays."""
    load_builtins()
    revisions = load_revisions()
    at_five = effective_rulebook(revisions, at=5)
    whole = effective_rulebook(revisions)
    assert whole.digest == shipped_rulebook().digest != at_five.digest
    for declared in REVISION["rules"]:  # type: ignore[union-attr]
        rule_id = str(declared["id"])  # type: ignore[index]
        now = whole.by_id(rule_id)
        assert now is not None
        assert now.version.split("+")[0] == declared["version"]  # type: ignore[index]
        before = at_five.by_id(rule_id)
        if before is not None:
            assert int(before.version.split("+")[0]) < int(now.version.split("+")[0]), rule_id


def test_the_new_stage_rules_publish_their_claims() -> None:
    """A defeasible rule naming no defeater publishes nothing, so each stage rule revision 6
    declares names the rules that state a file's own stage."""
    book = shipped_rulebook()
    folder = book.by_id("stage_inherited_from_folder")
    metadata = book.by_id("metadata_stage_from_its_acquisition")
    assert folder is not None and metadata is not None
    assert {"acquisition_stage_from_format", "pipeline_stage_from_name"} <= set(folder.defeated_by)
    assert "metadata_stage_from_its_acquisition" in folder.defeated_by
    assert "pipeline_stage_from_name" in metadata.defeated_by


def test_each_revision_6_module_declares_its_lists_and_has_a_docstring() -> None:
    from ingence.rulebook.revision_6 import derivations, experiments, workflows

    for module in (workflows, derivations, experiments):
        assert isinstance(module.RULES, list) and isinstance(module.CHANGES, list), module.__name__
        assert (module.__doc__ or "").strip(), module.__name__
