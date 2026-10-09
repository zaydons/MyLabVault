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


# Contrast of a Chart.js chart's axis text against the nearest opaque background behind its canvas.
# Canvas text is invisible to axe, so the chart's own options are checked instead.
CHART_CONTRAST = """canvas => {
    const chart = Chart.getChart(canvas);
    const rgb = color => {
        const ctx = document.createElement('canvas').getContext('2d');
        ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
        return Array.from(ctx.getImageData(0, 0, 1, 1).data);
    };
    let el = canvas, bg;
    while (el && (bg = getComputedStyle(el).backgroundColor).match(/rgba\\(.*, 0\\)|transparent/)) el = el.parentElement;
    const lum = ([r, g, b]) => [r, g, b].map(v => v / 255).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
    const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
    const back = rgb(bg), s = chart.options.scales;
    return {
        x: ratio(rgb(s.x.ticks.color), back), y: ratio(rgb(s.y.ticks.color), back),
        grid: rgb(s.y.grid.color)[3] / 255, dark: document.body.classList.contains('dark-mode'),
    };
}"""


def test_chart_text_contrast(page):
    ready(page)
    panel = api(page, "POST", "/api/panels/", {"name": "Metabolic"})["data"]
    lab = api(page, "POST", "/api/labs/", {"name": "Glucose", "panel_id": panel["id"], "ref_low": 70, "ref_high": 99})["data"]
    for value, when in ((88, "2025-01-10T08:00:00"), (104, "2025-06-10T08:00:00"), (92, "2026-01-10T08:00:00")):
        api(page, "POST", "/api/results/", {"lab_id": lab["id"], "patient_id": 1, "provider_id": 1, "result": value, "date_collected": when})
    api(page, "PUT", "/api/settings/user", {"dark_mode": True})
    page.goto(page.base + f"/charts?panel={panel['id']}", wait_until="networkidle")
    page.wait_for_function("() => Chart.getChart(document.querySelector('#panelChartsContent canvas'))")
    canvas = page.locator("#panelChartsContent canvas")

    # Dark on load, then light and dark again without a reload: drawn charts follow the theme
    for dark in (True, False, True):
        page.evaluate("d => document.body.classList.toggle('dark-mode', d)", dark)
        page.wait_for_timeout(400)  # let the card's colour transition finish
        result = canvas.evaluate(CHART_CONTRAST)
        assert result["dark"] is dark
        assert result["x"] >= 4.5 and result["y"] >= 4.5, result
        assert 0.05 <= result["grid"] <= 0.3, result  # grid lines visible but subtle


def test_chart_search_dropdowns(page):
    ready(page)
    for name in ("Chemistry", "Lipid Panel"):
        api(page, "POST", "/api/panels/", {"name": name})
    for name in ("mg/dL", "mmol/L"):
        api(page, "POST", "/api/units/", {"name": name})
    labs = {}
    for name, panel, unit in (("Glucose", 1, 1), ("Glucose (mmol/L)", 1, 2), ("Cholesterol, Total", 2, 1), ("HDL Cholesterol", 2, 1)):
        lab = api(page, "POST", "/api/labs/", {"name": name, "panel_id": panel, "unit_id": unit})
        labs[name] = lab["data"]["id"] if "data" in lab else lab["id"]
        for day, value in (("2025-01-02", 5.1), ("2025-06-02", 5.4)):
            api(page, "POST", "/api/results/", {"lab_id": labs[name], "patient_id": 1, "provider_id": 1, "result": value, "date_collected": day})

    page.goto(page.base + "/charts", wait_until="networkidle")
    assert page.is_hidden("#labSelect") and page.is_visible("#labSelect-search")
    assert page.get_attribute("label[for='labSelect-search']", "for") == "labSelect-search"

    # Every typed word must match, in any order, and the panel groups stay
    page.fill("#labSelect-search", "chol")
    options = lambda: page.eval_on_selector_all("#labSelect-list [role=option] .name", "els => els.map(e => e.textContent)")
    assert options() == ["Cholesterol, Total", "HDL Cholesterol"]
    assert page.eval_on_selector_all("#labSelect-list .search-select-group", "els => els.map(e => e.textContent)") == ["Lipid Panel"]
    page.fill("#labSelect-search", "total chol")
    assert options() == ["Cholesterol, Total"]
    page.fill("#labSelect-search", "zzz")
    assert page.text_content("#labSelect-list") == "No tests match"

    # Keyboard: filter, move down, Enter picks; the chart and address bar follow, and brackets stay in the title
    page.fill("#labSelect-search", "gluc")
    assert page.get_attribute("#labSelect-search", "aria-expanded") == "true"
    axe_clean(page, ".content")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_function("() => /Glucose \\(mmol\\/L\\)/.test(document.getElementById('individualChartTitle').textContent)")
    assert page.input_value("#labSelect-search") == "Glucose (mmol/L)"
    assert page.get_attribute("#labSelect-search", "aria-expanded") == "false"
    assert f"lab={labs['Glucose (mmol/L)']}" in page.url

    # Escape closes and puts back the current choice; the current choice is marked with a check icon, not only colour
    page.click("#labSelect-search")
    page.keyboard.type("hdl")
    page.keyboard.press("Escape")
    assert page.input_value("#labSelect-search") == "Glucose (mmol/L)"
    page.fill("#labSelect-search", "")
    assert page.eval_on_selector("#labSelect-list .current", "li => li.querySelector('.mdi-check') && li.textContent.includes('(current)')")

    # A mouse pick on the panel list loads that panel's charts
    page.fill("#panelSelect-search", "chem")
    page.click("#panelSelect-list [role=option]")
    page.wait_for_function("() => /Chemistry/.test(document.getElementById('panelChartsTitle').textContent)")
    assert page.input_value("#panelSelect-search") == "Chemistry" and "panel=1" in page.url

    # A link to a test opens it with its name in the box
    page.goto(page.base + f"/charts?lab={labs['HDL Cholesterol']}", wait_until="networkidle")
    assert page.input_value("#labSelect-search") == "HDL Cholesterol"
