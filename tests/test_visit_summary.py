"""Visit summaries read with AI (Bedrock mocked): vitals and medication changes on the review screen."""

from datetime import date, timedelta

import pdfs

# A recent visit (older documents are history: see test_old_document_medications_go_to_history)
VISIT = (date.today() - timedelta(days=20)).isoformat()
BEFORE = (date.today() - timedelta(days=21)).isoformat()
SUMMARY = pdfs.visit_summary()
AI_READING = {
    "visit_date": VISIT, "ordering_provider": "Alex Doe, PA-C", "tests": [],
    "vitals": [
        {"kind": "blood_pressure", "value": 128, "value2": 82, "unit": "mmHg"},
        {"kind": "temperature", "value": 98.4, "unit": "°F"},
        {"kind": "heart_rate", "value": 72, "unit": "bpm"},
        {"kind": "respiratory_rate", "value": 16, "unit": "/min"},
        {"kind": "weight", "value": 180.5, "unit": "lb"},
        {"kind": "height", "value": 70, "unit": "in"},
        {"kind": "oxygen_saturation", "value": 97, "unit": "%"},
        {"kind": "temperature", "value": 37, "unit": "furlongs"},  # unknown unit: left out
    ],
    "medications": [
        {"name": "Lisinopril", "dose": "10 mg", "frequency": "once daily", "route": "by mouth", "action": "new",
         "prescriber": "Alex Doe, PA-C", "reason": "Essential hypertension"},
        {"name": "Metformin HCL", "matched_medication": "Metformin", "dose": "1000 mg", "frequency": "twice daily", "action": "continue"},
        {"name": "Amlodipine", "dose": "5 mg", "action": "stop"},
        {"name": "Atorvastatin", "dose": "20mg", "action": "continue"},
        {"name": "Zolpidem", "dose": "5 mg", "action": "stop"},
    ],
}


def setup_list(client):
    """Alex Doe as a provider, three current medications and a weight already saved for the visit day."""
    client.post("/api/providers/", json={"name": "Alex Doe"})
    for name, dose in (("Metformin", "500 mg"), ("Amlodipine", "5 mg"), ("Atorvastatin", "20 mg")):
        assert client.post("/api/medications/", json={"patient_id": 1, "name": name, "dose": dose, "frequency": "once daily",
                                                      "start_date": "2025-01-01"}).status_code == 200
    client.post("/api/vitals/", json={"patient_id": 1, "vital_type": "weight", "value": 180.5, "unit": "lb",
                                      "measured_at": f"{VISIT}T09:00:00"})


def test_built_in_reader_finds_no_results():
    import asyncio
    from api.services.pdf_parser import PDFParser
    assert asyncio.run(PDFParser().parse_pdf_content(SUMMARY)).get("tests") == []


def test_review_and_save(client, ai):
    setup_list(client)
    ai.reply = AI_READING
    # The built-in reader finds no lab results, so the upload is read with AI
    p = client.post("/api/pdf/upload", files={"file": ("visit.pdf", SUMMARY, "application/pdf")}).json()
    assert p["parser"] == "ai" and p["date_collected"] == VISIT and p["total_tests_found"] == 0
    assert "Metformin" in ai.requests[-1]["messages"][0]["content"][1]["text"]  # current medications sent for matching
    assert p["matched_provider"]["name"] == "Alex Doe"

    vitals = {v["label"]: v for v in p["vitals"]}
    assert len(p["vitals"]) == 7 and vitals["Blood Pressure"]["reading"] == "128/82 mmHg"
    assert vitals["Respiratory Rate"]["reading"] == "16 breaths/min" and vitals["Height"]["reading"] == "5′ 10″"
    assert vitals["Weight"]["already_saved"] and not vitals["Heart Rate"]["already_saved"]
    plans = [(m["name"], m["plan"]) for m in p["medications"]]
    assert plans == [("Lisinopril", "add"), ("Metformin HCL", "change_dose"), ("Amlodipine", "stop"),
                     ("Atorvastatin", "none"), ("Zolpidem", "none")]
    assert "500 mg" in p["medications"][1]["note"] and "nothing to stop" in p["medications"][4]["note"]

    new_vitals = [v["index"] for v in p["vitals"] if not v["already_saved"]]
    r = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [{
        "import_id": p["import_id"], "selected_tests": [], "provider_id": 1,
        "selected_vitals": new_vitals, "selected_medications": [0, 1, 2]}]})
    data = r.json()["data"]
    assert r.status_code == 200 and data["failed_count"] == 0, r.text
    saved = data["files"][0]
    assert saved["vitals_count"] == 6 and [c["plan"] for c in saved["medication_changes"]] == ["add", "change_dose", "stop"]
    assert saved["dates"] == [VISIT]

    stored = client.get("/api/vitals/?patient_id=1").json()
    assert len(stored) == 7 and all(v["measured_at"].startswith(VISIT) for v in stored)
    meds = {(m["name"], m["dose"]): m for m in client.get("/api/medications/?patient_id=1").json()}
    lisinopril = meds[("Lisinopril", "10 mg")]
    assert lisinopril["start_date"] == VISIT and lisinopril["end_date"] is None and lisinopril["provider_name"] == "Alex Doe"
    assert meds[("Metformin", "500 mg")]["end_date"] == BEFORE
    assert meds[("Metformin", "1000 mg")]["start_date"] == VISIT and meds[("Metformin", "1000 mg")]["end_date"] is None
    assert meds[("Amlodipine", "5 mg")]["end_date"] == VISIT
    assert meds[("Atorvastatin", "20 mg")]["end_date"] is None

    # Saved rows are marked on the review; the import can no longer be re-read
    again = client.get(f"/api/pdf/review/{p['import_id']}").json()
    assert [v["already_imported"] for v in again["vitals"]] == [v["index"] in new_vitals for v in again["vitals"]]
    assert [m["already_imported"] for m in again["medications"]] == [True, True, True, False, False]
    assert client.post(f"/api/pdf/rescan-ai/{p['import_id']}").status_code == 400
    dup = client.post("/api/pdf/upload", files={"file": ("visit.pdf", SUMMARY, "application/pdf")}).json()
    assert dup["duplicate_warning"]


def test_visit_date_needed(client, ai):
    ai.reply = {**AI_READING, "visit_date": None, "medications": []}
    p = client.post("/api/pdf/upload", files={"file": ("v.pdf", pdfs.visit_summary(tag="no date"), "application/pdf")},
                    params={"ai": "true"}).json()
    assert p["date_collected"] is None
    body = {"import_id": p["import_id"], "selected_tests": [], "provider_id": 1, "selected_vitals": [0]}
    r = client.post("/api/pdf/confirm", json=body)
    assert r.status_code == 400 and "visit date" in r.json()["detail"]
    r = client.post("/api/pdf/confirm", json={**body, "manual_date": VISIT})
    assert r.status_code == 200 and r.json()["data"]["vitals_count"] == 1


def test_medication_plan_dates():
    from api.models import Medication
    from api.services.visit_review import plan_medication
    today = date(2026, 6, 15)
    current = [Medication(id=1, name="Metformin", dose="500 mg", start_date=date(2026, 5, 1))]
    later = plan_medication({"name": "Metformin", "dose": "1000 mg", "action": "continue"}, current, date(2026, 3, 2), today)
    assert later["plan"] == "history"  # a dose from before the one on the list: a past period, current untouched
    stop = plan_medication({"name": "Metformin", "action": "stop"}, current, date(2026, 3, 2), today)
    assert stop["plan"] == "none" and "later record" in stop["note"]
    assert plan_medication({"name": "metformin hcl", "dose": "500mg", "action": "new"}, current, date(2026, 6, 1), today)["plan"] == "none"


def test_medication_history_plans():
    from api.models import Medication
    from api.services.visit_review import plan_medication
    today = date(2026, 11, 1)
    periods = [Medication(id=1, name="Lisinopril", dose="10 mg", start_date=date(2025, 1, 1)),
               Medication(id=2, name="Atorvastatin", dose="20 mg", start_date=date(2019, 1, 1), end_date=date(2020, 6, 30))]
    plan = lambda med, visit=date(2026, 10, 20): plan_medication(med, periods, visit, today)  # noqa: E731
    # Printed stop date or inactive: a past period with its own dates, unless the history already covers them
    past = plan({"name": "Simvastatin", "dose": "10 mg", "action": "listed", "start_date": "2016-02-01", "stop_date": "2018-12-31"})
    assert past["plan"] == "history" and (past["start"], past["end"]) == (date(2016, 2, 1), date(2018, 12, 31))
    covered = plan({"name": "Atorvastatin", "dose": "20 mg", "action": "listed", "start_date": "2019-03-01", "stop_date": "2020-01-01"})
    assert covered["plan"] == "none" and "Already in your history" in covered["note"]
    assert plan({"name": "Ibuprofen", "status": "inactive", "action": "listed"})["plan"] == "history"
    # An old document never changes current medications
    old = date(2023, 4, 1)
    assert plan({"name": "Lisinopril", "dose": "10 mg", "action": "continue"}, old)["plan"] == "history"  # before it was on the list
    assert plan({"name": "Lisinopril", "dose": "20 mg", "action": "stop"}, old)["plan"] == "none"
    assert plan({"name": "Metoprolol", "dose": "25 mg", "action": "new"}, old)["plan"] == "history"
    # A recent document still adds, changes and stops
    assert plan({"name": "Metoprolol", "dose": "25 mg", "action": "new"})["plan"] == "add"
    assert plan({"name": "Lisinopril", "dose": "20 mg", "action": "continue"})["plan"] == "change_dose"
    assert plan({"name": "Lisinopril", "action": "stop"})["plan"] == "stop"


def test_old_document_medications_go_to_history(client, ai):
    client.post("/api/medications/", json={"patient_id": 1, "name": "Lisinopril", "dose": "10 mg", "start_date": "2025-01-01"})
    ai.reply = {"visit_date": "2023-04-01", "tests": [], "medications": [
        {"name": "Simvastatin", "dose": "10 mg", "action": "listed", "start_date": "2016-02-01", "stop_date": "2018-12-31"},
        {"name": "Metoprolol", "dose": "25 mg", "action": "continue"},
        {"name": "Lisinopril", "dose": "5 mg", "action": "stop"},
    ]}
    p = client.post("/api/pdf/upload", files={"file": ("old.pdf", pdfs.visit_summary(visit="04/01/2023", tag="old"), "application/pdf")},
                    params={"ai": "true"}).json()
    assert [(m["plan"], m["start_date"], m["stop_date"]) for m in p["medications"]] == [
        ("history", "2016-02-01", "2018-12-31"), ("history", None, None), ("none", None, None)]
    assert "dated by the document" in p["medications"][1]["note"]
    r = client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [], "provider_id": 1,
                                              "selected_medications": [0, 1, 2]}).json()["data"]
    assert [c["plan"] for c in r["medication_changes"]] == ["history", "history"]
    meds = {m["name"]: m for m in client.get("/api/medications/?patient_id=1").json()}
    assert (meds["Simvastatin"]["start_date"], meds["Simvastatin"]["end_date"]) == ("2016-02-01", "2018-12-31")
    assert (meds["Metoprolol"]["start_date"], meds["Metoprolol"]["end_date"]) == ("2023-04-01", "2023-04-01")
    assert meds["Lisinopril"]["end_date"] is None and meds["Lisinopril"]["dose"] == "10 mg"  # current one untouched
    # Importing the same history again finds it already there
    again = client.post("/api/pdf/upload", files={"file": ("old2.pdf", pdfs.visit_summary(visit="04/01/2023", tag="again"), "application/pdf")},
                        params={"ai": "true"}).json()
    assert [m["plan"] for m in again["medications"]] == ["none", "none", "none"]


def test_pdf_limits_for_the_ai_reader(client, ai):
    from reportlab.pdfgen import canvas
    import io
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    for i in range(101):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    r = client.post("/api/pdf/upload", files={"file": ("big.pdf", buffer.getvalue(), "application/pdf")}, params={"ai": "true"})
    assert r.status_code == 400 and "101 pages" in r.json()["detail"] and "Split it" in r.json()["detail"]
    assert ai.requests == []  # nothing sent


def test_provider_name_with_credential_after_comma():
    from api.routers.pdf_import import _name_tokens
    assert _name_tokens("Alex Q Doe, PA-C") == _name_tokens("Alex Doe") == ["alex", "doe"]
    assert _name_tokens("Doe, Alex MD") == ["alex", "doe"]  # still "Last, First"
