"""PDF import: reading, review rows, confirming, duplicates and re-uploads."""

import os

import pdfs
from api import paths
from api.services.import_review import parse_range_text, units_match


def upload(client, data, name="report.pdf", **params):
    response = client.post("/api/pdf/upload", files={"file": (name, data, "application/pdf")}, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, import_id, rows, **extra):
    body = {"import_id": str(import_id), "selected_tests": rows, "provider_id": 1, **extra}
    return client.post("/api/pdf/confirm", json=body)


def lab_names(client):
    return sorted(lab["name"] for lab in client.get("/api/labs/?limit=1000").json())


def test_unit_helpers():
    assert units_match("K/uL", "x10E3/uL")
    assert units_match("mIU/L", "uIU/mL")
    assert units_match("K/cumm", "x10E3/uL")
    assert units_match("gm/dL", "g/dL")
    assert not units_match("mg/dL", "mmol/L")
    assert parse_range_text(">59") == {"low": 59.0, "high": None, "text": ">59"}
    assert parse_range_text("70 - 99")["high"] == 99.0


def test_review_rows_and_confirm_rules(client):
    client.post("/api/panels/", json={"name": "Chemistry"})
    client.post("/api/units/", json={"name": "mg/dL"})
    client.post("/api/units/", json={"name": "K/uL"})
    client.post("/api/labs/", json={"name": "Glucose", "panel_id": 1, "unit_id": 1, "ref_low": 70, "ref_high": 99})
    client.post("/api/labs/", json={"name": "WBC", "panel_id": 1, "unit_id": 2, "ref_low": 3.4, "ref_high": 10.8})

    data = pdfs.lab_report([["Glucose", "5.9", "", "mmol/L", "3.9-5.5"], ["WBC", "6.1", "", "x10E3/uL", "3.4-10.8"],
                            ["Triglycerides", "210", "High", "mg/dL", "0-149"]], header_lines=["Patient report"])
    preview = upload(client, data, name="../../evil name.pdf")
    rows = {t["name"]: t for t in preview["tests"]}
    assert preview["date_collected"] is None
    assert rows["Glucose"]["unit_mismatch"] and "mmol/L" in rows["Glucose"]["issues"][0]
    assert rows["WBC"]["matched_lab_id"] == 2 and not rows["WBC"]["unit_mismatch"]
    assert rows["Triglycerides"]["status"] == "high" and rows["Glucose"]["status"] == "high"
    assert [t["index"] for t in preview["tests"]] == [0, 1, 2]
    assert client.get(preview["pdf_url"]).headers["content-type"] == "application/pdf"
    stored = [f for f in os.listdir(paths.UPLOADS_DIR) if "evil" in f]
    assert stored and all(f[12] == "_" and ".." not in f for f in stored)

    iid = preview["import_id"]
    r = confirm(client, iid, [0, 1, 2])
    assert r.status_code == 400 and "collection date" in r.json()["detail"]
    r = confirm(client, iid, [0, 1, 2], manual_date="2999-01-01")
    assert r.status_code == 400 and "future" in r.json()["detail"]
    r = confirm(client, iid, [0, 1, 2], manual_date="2026-01-15", provider_id=None)
    assert r.status_code == 400 and "provider" in r.json()["detail"]
    r = confirm(client, iid, [0, 1, 2], manual_date="2026-01-15", edits={"1": {"result": ""}})
    assert r.status_code == 400 and "enter the result" in r.json()["detail"]
    assert client.get("/api/results/").json()["total_count"] == 0

    edits = {"0": {"new_lab": True}, "1": {"result": "6.4"}, "2": {"reference_range": "0-149"}}
    data = confirm(client, iid, [0, 1, 2], manual_date="2026-01-15", edits=edits).json()["data"]
    assert data["imported_count"] == 3 and data["date_collected"] == "2026-01-15"
    names = sorted(x["name"] for x in data["results"])
    assert "Glucose (mmol/L)" in names
    wbc = next(x for x in data["results"] if x["name"] == "WBC")
    assert wbc["value"] == "6.4" and wbc["lab_id"] == 2
    assert {x["name"] for x in data["out_of_range"]} >= {"Triglycerides"}

    review = client.get(f"/api/pdf/review/{iid}").json()
    assert all(t["already_imported"] for t in review["tests"])
    again = confirm(client, iid, [0, 1, 2], manual_date="2026-01-15").json()["data"]
    assert again["imported_count"] == 0 and client.get("/api/results/").json()["total_count"] == 3


def test_unreadable_row_keeps_positions(client):
    data = pdfs.lab_report([["", "", "", "", ""], ["Glucose", "88", "", "mg/dL", "70-99"]],
                           header_lines=["Date Collected: 02/01/2026"])
    preview = upload(client, data)
    glucose = next(t for t in preview["tests"] if t["name"] == "Glucose")
    saved = confirm(client, preview["import_id"], [glucose["index"]]).json()["data"]["results"]
    assert saved[0]["name"] == "Glucose" and saved[0]["value"] == "88"


def test_no_copies_of_existing_tests(client):
    a = upload(client, pdfs.lab_report([["Albumin", "4.5", "", "g/dL", "3.5-5.0"], ["Glucose", "90", "", "mg/dL", "70-99"]],
                                       header_lines=["Date Collected: 01/02/2024"]), name="a.pdf")
    b = upload(client, pdfs.lab_report([["Albumin", "4.4", "", "g/dL", "3.5-5.0"], ["Glucose", "92", "", "mg/dL", "70-99"]],
                                       header_lines=["Date Collected: 03/04/2024"]), name="b.pdf")
    every_row_new = lambda p: {str(t["index"]): {"new_lab": True} for t in p["tests"]}
    r = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [
        {"import_id": p["import_id"], "selected_tests": [t["index"] for t in p["tests"]], "provider_id": 1, "edits": every_row_new(p)}
        for p in (a, b)]})
    assert r.status_code == 200, r.text
    assert lab_names(client) == ["Albumin", "Glucose"]

    # A different unit is kept apart, and later reports in that unit go to that copy
    e = upload(client, pdfs.lab_report([["Glucose", "5.1", "", "mmol/L", "3.9-5.5"]], header_lines=["Date Collected: 07/08/2024"]))
    assert e["tests"][0]["unit_mismatch"]
    confirm(client, e["import_id"], [0], edits=every_row_new(e))
    f = upload(client, pdfs.lab_report([["Glucose", "5.3", "", "mmol/L", "3.9-5.5"]], header_lines=["Date Collected: 09/10/2024"]))
    assert f["tests"][0]["matched_lab_name"] == "Glucose (mmol/L)" and not f["tests"][0]["unit_mismatch"]
    confirm(client, f["import_id"], [0], edits=every_row_new(f))
    assert lab_names(client) == ["Albumin", "Glucose", "Glucose (mmol/L)"]
    assert client.get("/api/results/").json()["total_count"] == 6


SUMMARY = [
    ("09/25/2024", "09/26/2024", "CBC", "WBC", "7.8", "K/uL", "3.8-11.5", ""),
    ("09/25/2024", "09/26/2024", "CBC", "Hemoglobin", "14.5", "g/dL", "13.1-17.5", ""),
    ("01/02/2025", "01/03/2025", "CBC", "WBC", "7.3", "K/uL", "3.8-11.5", ""),
    ("01/02/2025", "01/03/2025", "CBC", "Hemoglobin", "15.3", "g/dL", "13.1-17.5", ""),
    ("04/14/2025", "04/15/2025", "CBC", "WBC", "8.1", "K/uL", "3.8-11.5", ""),
    ("04/14/2025", "04/15/2025", "LIPID", "LDL", "160", "mg/dL", "0-99", "high"),
]


def test_health_summary_rows_keep_their_dates(client):
    preview = upload(client, pdfs.health_summary(SUMMARY), name="summary.pdf")
    tests = preview["tests"]
    assert len(tests) == 6
    assert sorted({t["date_collected"] for t in tests}) == ["2024-09-25", "2025-01-02", "2025-04-14"]
    assert preview["date_collected"] == "2025-04-14" and not preview["physician"]
    ldl = next(t for t in tests if t["name"] == "LDL")
    assert ldl["status"] == "high" and ldl["flag"] == "high"

    first = [t["index"] for t in tests if t["date_collected"] == "2024-09-25"]
    rest = [t["index"] for t in tests if t["date_collected"] != "2024-09-25"]
    r = confirm(client, preview["import_id"], first, edits={str(first[0]): {"date_collected": "2999-01-01"}})
    assert r.status_code == 400 and "future" in r.json()["detail"]
    data = confirm(client, preview["import_id"], first, edits={str(first[0]): {"date_collected": "2024-09-24"}}).json()["data"]
    assert data["dates"] == ["2024-09-24", "2024-09-25"]
    data = confirm(client, preview["import_id"], rest).json()["data"]
    assert data["dates"] == ["2025-01-02", "2025-04-14"]
    wbc = sorted(r["result"] for r in client.get("/api/results/?limit=100").json()["results"] if r["lab"]["name"] == "WBC")
    assert wbc == [7.3, 7.8, 8.1]
    assert lab_names(client).count("WBC") == 1  # one test across all dates

    # A later export of the same summary: results already saved are flagged and left out
    later = upload(client, pdfs.health_summary(SUMMARY, tag="later"), name="summary-later.pdf")
    saved = [t for t in later["tests"] if t["already_saved"]]
    assert len(saved) == 5  # the row moved to 09/24 isn't a duplicate on 09/25
    assert all("already saved" in " ".join(t["issues"]) for t in saved)


def test_reupload_is_read_again_until_something_is_saved(client):
    import json
    from api.database import SessionLocal
    from api.models import PDFImportLog

    data = pdfs.health_summary(SUMMARY)
    first = upload(client, data)
    iid = int(first["import_id"])
    # Pretend it was read before an update: one row, one date, by the AI
    db = SessionLocal()
    log = db.get(PDFImportLog, iid)
    log.parsed_data = json.dumps({"parser": "ai", "date_collected": "2025-04-14",
                                  "tests": [{"name": "WBC", "result": "8.1", "numeric_value": 8.1, "is_numeric": True}]})
    db.commit()
    db.close()

    again = upload(client, data)
    assert again["import_id"] == str(iid) and not again.get("duplicate_warning")
    assert len({t["date_collected"] for t in again["tests"]}) == 3
    assert again["comparison"]["ai_count"] == 1
    assert len(client.get("/api/pdf/history").json()) == 1

    confirm(client, iid, [0])
    third = upload(client, data)
    assert third["duplicate_warning"] and third["total_tests_found"] == 6


def test_delete_import_removes_results_and_file(client):
    preview = upload(client, pdfs.lab_report([["Glucose", "88", "", "mg/dL", "70-99"]]))
    confirm(client, preview["import_id"], [0])
    assert client.get("/api/results/").json()["total_count"] == 1
    assert client.delete(f"/api/pdf/{preview['import_id']}").status_code == 200
    assert client.get("/api/results/").json()["total_count"] == 0
    assert not os.listdir(paths.UPLOADS_DIR)
