"""Re-checking every import with AI (Bedrock mocked) to backfill vitals, medications, vaccines and diagnoses."""

import json
from datetime import date, timedelta

import pdfs

COLLECTED = (date.today() - timedelta(days=40)).isoformat()
REPORT_DATE = "2026-01-15"  # printed on the generated report
REPORT = pdfs.lab_report([["Cholesterol, Total", "185", "", "mg/dL", "100-199"], ["Glucose", "88", "", "mg/dL", "70-99"]],
                         tag="recheck all")
# What the AI finds on a fresh read: the same results, plus what the built-in reader never looked for
AI_READING = {
    "collection_date": REPORT_DATE,
    "tests": [{"name": "Cholesterol, Total", "value": 185, "unit": "mg/dL", "ref_text": "100-199"},
              {"name": "Glucose", "value": 88, "unit": "mg/dL", "ref_low": 70, "ref_high": 99, "ref_text": "70-99"}],
    "vitals": [{"kind": "heart_rate", "value": 64, "unit": "bpm"}],
    "medications": [{"name": "Lisinopril", "dose": "10 mg", "action": "continue"}],
    "immunizations": [{"vaccine": "Flu shot", "matched_vaccine": "Influenza (flu)", "date_given": COLLECTED}],
    "diagnoses": [{"name": "Essential hypertension", "icd10_code": "I10", "context": "problem_list"}],
}


def imported_report(client, tag="recheck all"):
    """A lab report imported with the built-in reader: only its results are saved."""
    report = pdfs.lab_report([["Cholesterol, Total", "185", "", "mg/dL", "100-199"], ["Glucose", "88", "", "mg/dL", "70-99"]], tag=tag)
    p = client.post("/api/pdf/upload", files={"file": (f"{tag}.pdf", report, "application/pdf")}).json()
    assert p["parser"] == "standard" and not p["diagnoses"]
    assert client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [0, 1], "provider_id": 1}).status_code == 200
    return int(p["import_id"])


def test_recheck_backfills_sections(client, ai):
    iid = imported_report(client)
    ai.reply = AI_READING
    c = client.post(f"/api/pdf/{iid}/recheck").json()
    assert c["summary"]["same"] == 2
    assert c["to_review"] == {"vitals": 1, "medications": 1, "vaccines": 1, "diagnoses": 1}

    # The review card now offers them, next to the results already imported
    p = client.get(f"/api/pdf/review/{iid}").json()
    assert all(t["already_imported"] for t in p["tests"])
    assert [d["plan"] for d in p["diagnoses"]] == ["add"] and p["immunizations"][0]["name"] == "Influenza (flu)"
    history = {h["id"]: h for h in client.get("/api/pdf/history").json()}
    assert history[iid]["to_review"] == 4

    r = client.post("/api/pdf/confirm", json={"import_id": str(iid), "selected_tests": [], "provider_id": 1,
                                               "selected_diagnoses": [0], "selected_immunizations": [0]})
    assert r.status_code == 200, r.text
    assert [d["name"] for d in client.get("/api/diagnoses/").json()] == ["Essential hypertension"]
    assert client.get("/api/diagnoses/").json()[0]["onset_date"] == REPORT_DATE  # dated by the report
    assert len(client.get("/api/vaccines/").json()) == 1
    assert {h["id"]: h for h in client.get("/api/pdf/history").json()}[iid]["to_review"] == 2  # the vital and medication

    # A section with saved rows keeps its reading: re-checking again doesn't move them
    ai.reply = {**AI_READING, "diagnoses": [{"name": "Something else", "context": "visit"}]}
    again = client.post(f"/api/pdf/{iid}/recheck").json()
    assert again["to_review"]["diagnoses"] == 0
    parsed = client.get(f"/api/pdf/review/{iid}").json()
    assert [d["name"] for d in parsed["diagnoses"]] == ["Essential hypertension"] and parsed["diagnoses"][0]["already_imported"]


def test_recheck_all(client, ai):
    first = imported_report(client, "first")
    second = imported_report(client, "second")
    hand = client.post("/api/pdf/manual/review", json={"tests": [{"name": "Glucose", "result": "90"}], "date_collected": COLLECTED})
    assert hand.status_code == 200  # nothing saved; entries by hand have no PDF to re-check anyway

    state = client.get("/api/pdf/recheck-all").json()
    assert state["eligible"] == 2 and state["never_rechecked"] == 2 and not state["running"]

    ai.reply = AI_READING
    r = client.post("/api/pdf/recheck-all")
    assert r.status_code == 200, r.text
    # The test client runs the background job before returning
    state = client.get("/api/pdf/recheck-all").json()
    assert not state["running"] and state["done"] == 2 and state["total"] == 2 and state["finished_at"]
    assert [(e["import_id"], e["status"]) for e in state["results"]] == [(first, "ok"), (second, "ok")]
    assert state["results"][0]["to_review"]["diagnoses"] == 1 and state["never_rechecked"] == 0
    assert all(h["to_review"] == 4 for h in client.get("/api/pdf/history").json())
    assert [d["plan"] for d in client.get(f"/api/pdf/review/{second}").json()["diagnoses"]] == ["add"]

    # Carrying on after a stop only reads the ones never re-checked
    assert client.post("/api/pdf/recheck-all", params={"skip_rechecked": "true"}).status_code == 400


def test_recheck_all_reports_problems(client, ai):
    imported_report(client, "fine")
    broken = imported_report(client, "broken")
    ai.status = 403  # the AI service refuses
    client.post("/api/pdf/recheck-all")
    state = client.get("/api/pdf/recheck-all").json()
    assert [e["status"] for e in state["results"]] == ["error", "error"]
    assert "AI parsing failed" in state["results"][1]["error"]

    # A file gone from disk is left out; nothing is changed by a failed read
    from api.models import PDFImportLog
    from api.database import SessionLocal
    from pathlib import Path
    db = SessionLocal()
    log = db.query(PDFImportLog).filter_by(id=broken).first()
    Path(log.file_path).unlink()
    assert "recheck" not in json.loads(log.parsed_data)
    db.close()
    assert client.get("/api/pdf/recheck-all").json()["eligible"] == 1


def test_recheck_all_needs_ai_and_one_at_a_time(client, ai, monkeypatch):
    from api.routers import pdf_import
    imported_report(client)
    monkeypatch.setitem(pdf_import.RECHECK_ALL, "running", True)
    assert client.post("/api/pdf/recheck-all").status_code == 409
    assert client.post("/api/pdf/recheck-all/stop").json()["stop_requested"] is True
    monkeypatch.setattr(pdf_import.ai_parser, "is_enabled", lambda: False)
    monkeypatch.setitem(pdf_import.RECHECK_ALL, "running", False)
    assert client.post("/api/pdf/recheck-all").status_code == 400


def test_merge_sections():
    from api.services.recheck import merge_sections
    parsed = {"diagnoses": [{"name": "A"}], "saved_diagnoses": [0], "vitals": [], "date_collected": None}
    reading = {"diagnoses": [{"name": "B"}], "vitals": [{"vital_type": "weight"}], "medications": [], "date_collected": "2025-01-01"}
    assert merge_sections(parsed, reading) == ["vitals"]
    assert parsed["diagnoses"] == [{"name": "A"}] and parsed["vitals"] == [{"vital_type": "weight"}]
    assert parsed["medications"] == [] and "immunizations" not in parsed and parsed["date_collected"] == "2025-01-01"


def test_stored_recheck_lists_what_to_review(client, ai):
    iid = imported_report(client, "stored")
    ai.reply = AI_READING
    client.post(f"/api/pdf/{iid}/recheck")
    stored = client.get(f"/api/pdf/{iid}/recheck").json()
    assert stored["summary"]["same"] == 2 and stored["to_review"]["diagnoses"] == 1


def test_reset_forgets_the_last_recheck_all(client, ai):
    imported_report(client, "reset")
    ai.reply = AI_READING
    client.post("/api/pdf/recheck-all")
    assert client.get("/api/pdf/recheck-all").json()["results"]
    assert client.post("/api/settings/reset-data").status_code == 200
    state = client.get("/api/pdf/recheck-all").json()
    assert state["results"] == [] and not state["running"] and state["eligible"] == 0
