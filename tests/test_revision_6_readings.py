"""What revision 6 reads that revision 5 did not, each the minimal synthetic case, every name
invented.

- A file whose own name states no stage takes the one stage its folder's name states, one hop up;
  a file stating its own, a folder stating two, and a folder two levels up give nothing.
- An instrument's metadata, named as the acquisition beside it but for the extension, is raw
  data and not a second acquisition, and a folder of movies and their metadata is no experiment.
"""

from __future__ import annotations

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
DAY = "vorn/2024-03-04 kelt dapi"
MOVIES = "vorn/2024-05-06_trell_actin"

#: The pool: one dated experiment folder holding every case, with what each file holds.
FILES: dict[str, str] = {
    f"{DAY}/images/kelt_001.nd2": "x",
    f"{DAY}/images/kelt_001.xml": "<a/>",
    f"{DAY}/images/kelt_002.nd2": "x",
    f"{DAY}/images/kelt_002.xml": "<a/>",
    f"{DAY}/proc/kelt_001 merge.tif": "x",
    f"{DAY}/proc/kelt_002_analysis.tif": "x",
    f"{DAY}/proc/deep/kelt_003.tif": "x",
    f"{DAY}/raw analysis/kelt_004.tif": "x",
    f"{DAY}/results/kelt_counts.csv": "a,b\n1,2\n",
    **{f"{DAY}/plates/zib-cofilin-R{index}-proc.tif": "x" for index in range(1, 12)},
    **{f"{MOVIES}/plates/zib-cofilin-R{index}-proc.tif": "x" for index in range(1, 3)},
    # A day of movies and their metadata alone, as an instrument wrote them.
    f"{MOVIES}/raw/Image0965.nd2": "x",
    f"{MOVIES}/raw/Image0965.xml": "<a/>",
    f"{MOVIES}/raw/Image0966.nd2": "x",
    f"{MOVIES}/raw/Image0966.xml": "<a/>",
}


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("revision_6")
    pool = base / "pool"
    for name, text in FILES.items():
        path = pool / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _claims(project: Project, rule: str) -> dict[str, str]:
    """Each entry's resolved claim of one rule, by path."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule],
        ).fetchall()
    return {str(path): str(value) for path, value in rows}


def test_a_file_stating_no_stage_takes_its_folder_s(project: Project) -> None:
    folder = _claims(project, "stage_inherited_from_folder")
    assert folder[f"{DAY}/results/kelt_counts.csv"] == "analysis"
    # The file's own name says it is analysis: the name rule's to say, never the folder's.
    assert f"{DAY}/proc/kelt_002_analysis.tif" not in folder
    # A merge is a step done at a stage and names none, so the folder's stage is taken.
    assert folder[f"{DAY}/proc/kelt_001 merge.tif"] == "processed"
    # One hop and never two: ``deep`` states nothing, and ``proc`` above it is not read.
    assert f"{DAY}/proc/deep/kelt_003.tif" not in folder
    # A folder's name stating two stages states none.
    assert f"{DAY}/raw analysis/kelt_004.tif" not in folder


def test_an_instrument_s_metadata_is_raw_and_no_second_acquisition(project: Project) -> None:
    metadata = _claims(project, "metadata_stage_from_its_acquisition")
    assert metadata == {
        f"{DAY}/images/kelt_001.xml": "raw",
        f"{DAY}/images/kelt_002.xml": "raw",
        f"{MOVIES}/raw/Image0965.xml": "raw",
        f"{MOVIES}/raw/Image0966.xml": "raw",
    }
    # Its folder is the acquisition's: the folder rule is defeated, so nothing conflicts.
    assert f"{DAY}/images/kelt_001.xml" not in _claims(project, "stage_inherited_from_folder")
    # The merge is still named for the movie, which stays the one acquisition of its name.
    named = _claims(project, "named_for_an_acquisition")
    assert named[f"{DAY}/proc/kelt_001 merge.tif"] == named[f"{DAY}/images/kelt_001.nd2"]


def test_a_folder_of_movies_and_their_metadata_is_no_experiment(project: Project) -> None:
    contents = _claims(project, "experiment_folder_from_contents")
    assert contents.get(f"{DAY}/images") != "experiment"
    # Version 3 read each metadata file as named for its movie, and called this folder one.
    assert contents.get(f"{MOVIES}/raw") == "acquisitions"


def test_the_metadata_beside_an_acquisition_is_found_by_name_alone() -> None:
    """The pass hands each file the one acquisition of its own name beside it, and none where
    two formats of one name stand beside it."""
    from ingence.infer.enclosing_folder import stem_of

    assert stem_of("Kelt_001.XML", "xml") == "kelt_001"
    assert stem_of("reads.fastq.gz", "fastq.gz") == "reads"
    assert stem_of("notes", None) == "notes"


def test_two_replicates_files_are_never_one_made_from_the_other(tmp_path) -> None:
    """``rep1`` and ``rep2`` are replicates' labels the Dictionary knows: the identity rule leaves
    the two files apart, and still pairs a file carrying no label with either."""
    from ingence.infer.records import assemble_entry_records
    from ingence.rulebook.predicates.propose import _replicate_labels
    from ingence.rulebook.registry import Reads

    pool = tmp_path / "pool"
    names = ("zib_rep1_proc.tif", "zib_rep2_analysis.csv", "zib_analysis.csv")
    for name in names:
        (pool / "day" / name).parent.mkdir(parents=True, exist_ok=True)
        (pool / "day" / name).write_text("x")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    with open_catalog(created.path) as catalog:
        records = {
            held.entry.name: held for held in assemble_entry_records(catalog, reads={Reads.RECORDS})
        }
    labels = {name: _replicate_labels(records[name]) for name in names}
    assert labels["zib_rep1_proc.tif"] == {("replicate", 1)}
    assert labels["zib_rep2_analysis.csv"] == {("replicate", 2)}
    assert labels["zib_analysis.csv"] == frozenset()
