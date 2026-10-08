"""Key screens in a real browser (Playwright), with an axe accessibility scan in light and dark mode.

Needs Chromium for Playwright (`python -m playwright install chromium`) and axe-core
(`npm install --prefix tests/.node axe-core@4.10.2`); see tests/README.md.
"""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

import pdfs
from conftest import app, reset_data

AXE = Path(os.getenv("AXE_JS", Path(__file__).parent / ".node/node_modules/axe-core/axe.min.js"))
playwright_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        # The Claude Code cloud image ships Chromium here; elsewhere Playwright uses its own download
        exe = "/opt/pw-browsers/chromium/chrome-linux/chrome"
        b = p.chromium.launch(executable_path=exe) if os.path.exists(exe) and not os.getenv("CI") else p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    reset_data()
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    # Pages must work offline: anything not served by the app is blocked
    context.route(lambda url: not url.startswith(server), lambda route: route.abort())
    pg = context.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.base = server
    yield pg
    context.close()
    assert not errors, errors


def api(page, method, path, body=None):
    response = page.request.fetch(page.base + path, method=method, data=body)
    assert response.ok, response.text()
    return response.json()


def ready(page):
    api(page, "POST", "/api/setup/", {"name": "Sal"})
    api(page, "POST", "/api/providers/", {"name": "Dr A"})


def axe_clean(page, selector):
    assert AXE.exists(), f"axe-core not found at {AXE}; run: npm install --prefix tests/.node axe-core@4.10.2"
    page.add_script_tag(path=str(AXE))
    problems = {}
    for dark in (False, True):
        page.evaluate("d => document.body.classList.toggle('dark-mode', d)", dark)
        page.mouse.move(0, 0)
        page.wait_for_timeout(400)  # let colour transitions finish
        violations = page.evaluate(f"async () => (await axe.run(document.querySelector({selector!r}))).violations"
                                   ".map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).slice(0, 3).join(' | '))")
        if violations:
            problems["dark" if dark else "light"] = violations
    page.evaluate("document.body.classList.remove('dark-mode')")
    assert not problems, problems


def test_welcome_sets_patient(page):
    page.goto(page.base + "/dashboard")
    assert "/welcome" in page.url
    page.fill("#welcomeName", "Sal")
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard")
    cookie = next(c for c in page.context.cookies() if c["name"] == "selectedPatientId")
    assert cookie["value"] == "1" and cookie["sameSite"] == "Strict"


def test_import_review(page, tmp_path):
    ready(page)
    report = tmp_path / "report.pdf"
    report.write_bytes(pdfs.lab_report([["Glucose", "88", "", "mg/dL", "70-99"], ["Triglycerides", "210", "High", "mg/dL", "0-149"]]))
    page.goto(page.base + "/import", wait_until="networkidle")
    page.set_input_files("#fileInput", str(report))
    page.wait_for_selector(".review-card")
    assert page.eval_on_selector_all(".row-name", "els => els.map(e => e.value)") == ["Glucose", "Triglycerides"]
    axe_clean(page, ".content")
    page.select_option("select[id^='provider-']", "1")
    page.click("#importButton")
    page.wait_for_selector("#importSummary:not([hidden])")
    assert "Imported 2 results" in page.text_content("#importSummary")

    # A second report on the same page offers the test the first import created
    second = tmp_path / "second.pdf"
    second.write_bytes(pdfs.lab_report([["Glucose", "91", "", "mg/dL", "70-99"]], header_lines=["Date Collected: 02/15/2026"]))
    page.set_input_files("#fileInput", str(second))
    page.wait_for_selector(".review-card")
    chosen = page.eval_on_selector(".row-lab", "s => s.options[s.selectedIndex].text")
    assert chosen.startswith("Glucose")


def test_health_summary_groups_by_date(page, tmp_path):
    ready(page)
    summary = tmp_path / "summary.pdf"
    summary.write_bytes(pdfs.health_summary([
        ("09/25/2024", "09/26/2024", "CBC", "WBC", "7.8", "K/uL", "3.8-11.5", ""),
        ("01/02/2025", "01/03/2025", "CBC", "WBC", "7.3", "K/uL", "3.8-11.5", ""),
    ]))
    page.goto(page.base + "/import", wait_until="networkidle")
    page.set_input_files("#fileInput", str(summary))
    page.wait_for_selector(".group-date")
    assert page.eval_on_selector_all(".group-date", "els => els.map(e => e.value)") == ["2024-09-25", "2025-01-02"]
    axe_clean(page, ".review-card")


def test_settings_sections(page):
    ready(page)
    page.goto(page.base + "/settings", wait_until="networkidle")
    for section in ("general", "backup", "cleanup", "about", "danger"):
        page.click(f"a[data-section='{section}']")
        visible = page.eval_on_selector_all(".settings-section", "s => s.filter(x => !x.hidden).map(x => x.id)")
        assert visible == [section]
        if section == "about":
            page.wait_for_selector("#systemInfo dl")
        axe_clean(page, ".content")
    page.goto(page.base + "/settings#backup", wait_until="networkidle")
    assert page.eval_on_selector_all(".settings-section", "s => s.filter(x => !x.hidden).map(x => x.id)") == ["backup"]


def test_vital_units(page):
    ready(page)
    for value, unit, when in ((150, "lb", "2026-01-01T08:00"), (70, "kg", "2026-02-01T08:00")):
        api(page, "POST", "/api/vitals/", {"patient_id": 1, "vital_type": "weight", "value": value, "unit": unit, "measured_at": when})
    page.goto(page.base + "/settings#general", wait_until="networkidle")
    page.wait_for_selector("#unit-weight")
    page.click("[data-unit-preset='metric']")
    page.wait_for_function("() => /Saved/.test(document.getElementById('vitalUnitsStatus').textContent)")
    page.goto(page.base + "/vitals", wait_until="networkidle")
    page.wait_for_selector("#latestCards .small-box")
    cards = page.text_content("#latestCards")
    assert "70 kg" in cards and "+2 kg since previous" in cards
    assert "entered as 150 lb" in page.text_content("#historyBody")
    axe_clean(page, ".content")
