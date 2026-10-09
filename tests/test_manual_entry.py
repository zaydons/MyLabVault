"""Results entered by hand on the Import page: pasted rows, live checks and saving like a report."""

import logging

from api.services.import_review import parse_pasted


def setup_tests(client):
    client.post("/api/panels/", json={"name": "Chemistry"})
    client.post("/api/units/", json={"name": "mg/dL"})
    client.post("/api/units/", json={"name": "K/uL"})
    client.post("/api/labs/", json={"name": "Glucose", "panel_id": 1, "unit_id": 1, "ref_low": 70, "ref_high": 99})
    client.post("/api/labs/", json={"name": "WBC", "panel_id": 1, "unit_id": 2})


def save(client, rows, edits=None, key=1, **extra):
    item = {"key": key, "manual": {"tests": rows, "panel_name": extra.pop("panel", None)}, "selected_tests": list(range(len(rows))),
            "provider_id": 1, "manual_date": "2026-01-15", "edits": edits or {}, **extra}
    response = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [item]})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def lab_names(client):
    return sorted(lab["name"] for lab in client.get("/api/labs/?limit=1000").json())


def test_parse_pasted():
    rows = parse_pasted("""Glucose\t105\tmg/dL\t70-99
        WBC  6.1  H  x10E3/uL  3.4-10.8
        - Hemoglobin A1c 5.4 % 4.8-5.6
        Vitamin D, 25-Hydroxy 32.1 ng/mL 30.0 - 100.0
        HDL Cholesterol 52 mg/dL >39
        HIV Screen Negative
        -----
        Page 2 of 3
        """)
    assert [(r["name"], r["result"], r["unit"], r["reference_range"]) for r in rows] == [
        ("Glucose", "105", "mg/dL", "70-99"),
        ("WBC", "6.1", "x10E3/uL", "3.4-10.8"),              # the H/L column is dropped; the range decides
        ("Hemoglobin A1c", "5.4", "%", "4.8-5.6"),          # a digit inside the name isn't the result
        ("Vitamin D, 25-Hydroxy", "32.1", "ng/mL", "30.0 - 100.0"),
        ("HDL Cholesterol", "52", "mg/dL", ">39"),
        ("HIV Screen", "Negative", "", ""),
    ]


def test_rows_are_checked_as_typed(client):
    setup_tests(client)
    rows = [{"name": "Glucose", "result": "105", "unit": "mg/dL", "reference_range": "70-99"},
            {"name": "Glucose", "result": "5.9", "unit": "mmol/L"},
            {"name": "WBC", "result": "6.1", "unit": "x10E3/uL"},
            {"name": "Ferritin", "result": ""}]
    checked = client.post("/api/pdf/manual/review", json={"tests": rows, "date_collected": "2026-01-15"}).json()["tests"]
    assert checked[0]["matched_lab_id"] == 1 and checked[0]["status"] == "high" and not checked[0]["issues"]
    assert checked[1]["unit_mismatch"] and checked[1]["issues"] == ["You entered mmol/L, but Glucose is saved in mg/dL."]
    assert checked[2]["matched_lab_id"] == 2 and not checked[2]["unit_mismatch"]   # K/uL = x10E3/uL
    assert checked[3]["matched_lab_id"] is None and checked[3]["issues"] == []      # not "couldn't be read"

    pasted = client.post("/api/pdf/manual/paste", json={"text": "Glucose  88  mg/dL  70-99\nTSH 2.1 mIU/L 0.45-4.5"}).json()["tests"]
    assert [(t["name"], t["matched_lab_id"], t["status"]) for t in pasted] == [("Glucose", 1, "normal"), ("TSH", None, "normal")]


def test_saved_like_a_report(client, caplog):
    setup_tests(client)
    caplog.set_level(logging.INFO)
    rows = [{"name": "Glucose", "result": "105", "unit": "mg/dL", "reference_range": "70-99"},
            {"name": "WBC", "result": "6.1", "unit": "x10E3/uL"},
            {"name": "Hemoglobin A1c", "result": "5.4", "unit": "%", "reference_range": "4.8-5.6"},
            {"name": "HIV Screen", "result": "Negative"}]
    data = save(client, rows, {"0": {"lab_id": 1}, "1": {"lab_id": 2}, "2": {"new_lab": True}, "3": {"new_lab": True}}, key=7, panel="Chemistry")
    entry = data["files"][0]
    assert data["total_imported"] == 4 and entry["key"] == 7 and entry["filename"] == "Entered by hand"
    assert [r["name"] for r in entry["out_of_range"]] == ["Glucose"]
    labs = {lab["name"]: lab for lab in client.get("/api/labs/?limit=100").json()}
    assert labs["Hemoglobin A1c"]["panel_name"] == "Chemistry"                     # the panel chosen for new tests
    results = client.get("/api/results/?limit=100").json()["results"]
    hiv = next(r for r in results if r["lab"]["name"] == "HIV Screen")
    assert hiv["result_text"] == "Negative" and hiv["date_collected"].startswith("2026-01-15")
    assert "import.confirmed" in caplog.text and "source=manual" in caplog.text

    # The same results typed again on another visit go into the same tests, never copies
    save(client, rows, {str(i): {"new_lab": True} for i in range(4)}, manual_date="2026-02-15")
    assert lab_names(client) == ["Glucose", "HIV Screen", "Hemoglobin A1c", "WBC"]
    # A different unit is kept apart, as it is for reports
    save(client, [{"name": "Glucose", "result": "5.9", "unit": "mmol/L"}], {"0": {"new_lab": True}})
    assert "Glucose (mmol/L)" in lab_names(client)

    # Entered by hand shows in the import history, without a PDF
    history = client.get("/api/pdf/history").json()
    assert {(h["filename"], h["status"], h["has_file"]) for h in history} == {("Entered by hand", "completed", False)}
    review = client.get(f"/api/pdf/review/{history[0]['id']}").json()
    assert review["pdf_url"] is None and all(t["already_imported"] for t in review["tests"])
    # ...and a typed result already saved on that date is flagged
    again = client.post("/api/pdf/manual/review", json={"tests": rows[:1], "date_collected": "2026-01-15"}).json()["tests"][0]
    assert again["already_saved"]


def test_nothing_saved_when_a_row_is_incomplete(client):
    setup_tests(client)
    response = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [{
        "key": 3, "manual": {"tests": [{"name": "Glucose", "result": "90"}, {"name": "TSH", "result": ""}]},
        "selected_tests": [0, 1], "provider_id": 1, "manual_date": "2026-01-15", "edits": {}}]}).json()["data"]
    assert response["failed_files"][0]["key"] == 3 and "TSH: enter the result" in response["failed_files"][0]["error"]
    assert client.get("/api/results/").json()["total_count"] == 0
    assert client.get("/api/pdf/history").json() == []          # no empty entry left in the history


def test_old_address_redirects(client):
    response = client.get("/bulk-import", follow_redirects=False)
    assert response.status_code == 301 and response.headers["location"] == "/import?manual=1"
