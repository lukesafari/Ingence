"""The Dashboard keeps other web pages out of a project, and its exports keep formulas out of
spreadsheets.

- A read of the API that a page on another site starts is refused before any work is done. The
  browser would hide the answer, but the server would already have looked at a folder named in the
  request (on Windows a ``\\\\host\\share`` name hands that host the person's sign-in), opened a
  project, or started a long query.
- No page may show the Dashboard in a frame: every write is one click, and a nearly invisible frame
  could steer a person's clicks into the Record.
- The reorganisation notice compares two runs this project holds, never a path spelled out of it.
- The groupings export writes text a spreadsheet would run as a formula as the text it is.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.server import DashboardServer
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

R0 = "run_20260101T000001Z_111111111111"
R1 = "run_20260101T000002Z_222222222222"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("hardening")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_vorn_01.csv").write_text("a\n1\n", encoding="utf-8")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(made, run_id=R0, started_at="2026-01-01T00:00:01Z")
    run_inference(made, run_id=R1, started_at="2026-01-01T00:00:02Z")
    return made.path


def _get(server: DashboardServer, path: str, **headers: str) -> tuple[int, dict[str, str], Any]:
    request = urllib.request.Request(f"{server.url.rstrip('/')}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, dict(error.headers), json.loads(body) if body else None


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://elsewhere.example"},
        {"Origin": "null"},
    ],
)
def test_a_read_another_site_started_is_refused_before_it_does_anything(
    project: Path, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api.routes import landing

    looked: list[object] = []
    monkeypatch.setattr(landing, "_expanded", lambda *args, **kwargs: looked.append(args))
    with DashboardServer(project, port=0) as server:
        status, _, body = _get(server, "/api/landing/check?path=/somewhere", **headers)
    assert status == 403
    assert "another site" in body["error"]
    assert looked == []


@pytest.mark.parametrize(
    "headers", [{}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}]
)
def test_its_own_pages_a_typed_address_and_a_command_still_read(
    project: Path, headers: dict[str, str]
) -> None:
    with DashboardServer(project, port=0) as server:
        status, _, _ = _get(server, "/api/runs", **headers)
    assert status == 200


def test_no_page_may_show_the_dashboard_in_a_frame(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        _, headers, _ = _get(server, "/api/runs")
        page = urllib.request.urlopen(server.url, timeout=60)
        page_headers = dict(page.headers)
        page.close()
    for said in (headers, page_headers):
        assert said["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in said["Content-Security-Policy"]
        assert said["X-Content-Type-Options"] == "nosniff"


def test_the_reorganisation_notice_compares_only_runs_this_project_holds(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, _, body = _get(
            server, f"/api/reorganisation?before=../../elsewhere/runs/{R0}&after={R1}"
        )
    assert status >= 400
    assert "runs this project holds" in json.dumps(body)


def test_the_groupings_export_writes_a_formula_as_text(project: Path) -> None:
    from ingence.api.routes.workspace import _groupings_export

    append_event(
        project,
        kind=RecordEventKind.NOTE,
        actor='=HYPERLINK("https://x.example","open")',
        occurred_at="2026-01-01T00:00:03Z",
        target="group:kelt",
        against_run_id=R1,
        payload={"note": "+SUM(1,2)"},
    )
    text = _groupings_export(project)["text"]
    assert "'=HYPERLINK" in text and "'+SUM(1,2)" in text
    assert ",=HYPERLINK" not in text and ",+SUM" not in text


def test_a_themes_table_writes_a_formula_folder_name_as_text_and_numbers_as_numbers() -> None:
    from ingence.themes.export import _cell

    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _cell("-cmd") == "'-cmd"
    assert _cell(-3) == "-3"
    assert _cell(2.5) == "2.5"
    assert _cell(["=a", "b"]).startswith("'=")
