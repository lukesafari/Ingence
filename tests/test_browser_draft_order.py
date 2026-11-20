"""The newest value typed is the one the draft keeps, whatever order the saves land in.

A page saved 3, then 7, and the server could meet the 7 first and the 3 after it. The draft then
held 3 while the page said "saved" and counted nothing waiting. The tests hold the first save on its
way, type a newer value, and let the first go: the draft holds the newer value. A save that fails
stays held and is sent again, and the Commit dialog does not open over an edit that did not reach
the draft.

A field is made on the page by the test, as the Settings page makes its own
(``data-draft="settings:scan.workers"``), so the tests do not depend on that page's layout.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import launch_browser, need, start_dashboard, stop

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

sync_api = need("playwright.sync_api", "the browser extra is not installed")

DRAFT = "**/api/workspace/draft"

#: A field like the Settings page's own, for one whole-number setting.
FIELD = """() => {
  const input = document.createElement('input');
  input.type = 'number';
  input.id = 'probe-workers';
  input.dataset.draft = 'settings:scan.workers';
  input.dataset.original = '4';
  const cell = document.createElement('td');
  cell.appendChild(input);
  document.body.appendChild(cell);
}"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_07.csv").write_text("well,value\nA1,3\n", encoding="utf-8")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="t")
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(made.path, config_home)
    try:
        yield address.rstrip("/"), made.path
    finally:
        stop(process)


def _drafted(project: Path) -> Any:
    path = project / "workspace" / "drafts" / "settings.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text("utf-8"))["values"].get("scan", {}).get("workers")


def _page(browser: Any, address: str) -> Any:
    page = browser.new_page()
    page.goto(f"{address}/#overview")
    page.wait_for_function("() => typeof saveDrafts === 'function'")
    page.evaluate(FIELD)
    return page


def _type(page: Any, value: str) -> None:
    page.fill("#probe-workers", value)


def _wait_until(page: Any, check: Any, seconds: float = 10.0) -> None:
    """Wait for ``check`` while Playwright goes on handling the page's requests (a plain sleep
    would stop it calling the route handlers)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError("the condition did not hold in time")


def test_a_newer_value_typed_while_an_older_save_is_on_its_way_is_the_one_kept(
    browser: Any, served: tuple[str, Path]
) -> None:
    address, project = served
    page = _page(browser, address)
    held: list[Any] = []
    sent: list[int] = []

    def hold_the_first(route: Any) -> None:
        body = json.loads(route.request.post_data or "{}")
        sent.append(body["values"]["scan"]["workers"])
        if len(sent) == 1:
            held.append(route)
            return
        route.continue_()

    try:
        page.route(DRAFT, hold_the_first)
        _type(page, "3")
        # Not awaited: the save waits on the request held below.
        page.evaluate("() => { saveDrafts('settings'); }")
        _wait_until(page, lambda: bool(held))
        # The first save is on its way and not answered. A newer value is typed and saved.
        _type(page, "7")
        page.evaluate("() => { saveDrafts('settings'); }")
        page.wait_for_timeout(400)
        # One save at a time: the newer one waits for the first, and has not been sent.
        assert sent == [3]
        held[0].continue_()
        _wait_until(page, lambda: _drafted(project) == 7)
        page.wait_for_function("() => !savingDrafts.size")
        assert sent == [3, 7]
        assert _drafted(project) == 7
        assert page.evaluate("() => heldEdits('settings').edits.size") == 0
    finally:
        page.close()


def test_a_save_that_fails_is_kept_and_sent_again_with_what_was_typed_since(
    browser: Any, served: tuple[str, Path]
) -> None:
    address, project = served
    page = _page(browser, address)
    answered: list[int] = []

    def fail_the_first(route: Any) -> None:
        answered.append(1)
        if len(answered) == 1:
            route.fulfill(status=503, content_type="application/json", body='{"error": "busy"}')
            return
        route.continue_()

    try:
        page.route(DRAFT, fail_the_first)
        _type(page, "3")
        failed = page.evaluate(
            "() => saveDrafts('settings').then(() => 'saved', (error) => String(error))"
        )
        assert failed != "saved"
        assert page.evaluate("() => heldEdits('settings').edits.get('scan.workers')") == 3
        assert _drafted(project) is None
        _type(page, "5")
        assert page.evaluate("() => saveDrafts('settings').then(() => 'saved')") == "saved"
        assert _drafted(project) == 5
    finally:
        page.close()


def test_a_save_older_than_one_already_landed_from_the_page_changes_nothing(
    served: tuple[str, Path],
) -> None:
    """The request a closing page sends can land before the save still on its way: the server
    keeps the page's last number and applies nothing numbered at or below it."""
    address, project = served

    def save(workers: int, sequence: int) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{address}/api/workspace/draft",
            data=json.dumps(
                {
                    "kind": "settings",
                    "values": {"scan": {"workers": workers}},
                    "removed": [],
                    "writer": "page-probe",
                    "sequence": sequence,
                }
            ).encode(),
            headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            answer: dict[str, Any] = json.loads(response.read())
            return answer

    newer = save(7, 2)
    older = save(3, 1)
    assert newer["stale"] is False
    assert older["stale"] is True
    assert older["revision"] == newer["revision"]
    assert _drafted(project) == 7


def test_the_commit_dialog_does_not_open_over_an_edit_that_could_not_be_saved(
    browser: Any, served: tuple[str, Path]
) -> None:
    address, project = served
    page = _page(browser, address)
    try:
        page.route(
            DRAFT,
            lambda route: route.fulfill(
                status=503, content_type="application/json", body='{"error": "busy"}'
            ),
        )
        _type(page, "6")
        page.evaluate("() => openCommitDialog()")
        assert page.locator(".commit-dialog").count() == 0
        assert _drafted(project) is None
        assert page.evaluate("() => heldEdits('settings').edits.get('scan.workers')") == 6
    finally:
        page.close()
