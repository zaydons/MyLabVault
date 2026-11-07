"""Diagnoses: the patient's conditions, entered by hand or imported from summaries read with AI (Bedrock mocked)."""

from datetime import date, timedelta

import pdfs

VISIT = (date.today() - timedelta(days=20)).isoformat()


def add(client, **fields):
    body = {"patient_id": 1, "name": "Essential hypertension", "icd10_code": " i10 ", "onset_date": "2024-03-01", **fields}
    response = client.post("/api/diagnoses/", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_record_resolve_and_validate(client):
    first = add(client, provider_id=1, notes="  ")
    assert first["icd10_code"] == "I10" and first["status"] == "active" and first["notes"] is None and first["provider_name"]
    add(client, name="Acute sinusitis", icd10_code="J01.90", status="resolved", onset_date="2023-01-02", resolved_date="2023-01-20")

    # Validation: a name, no future dates, not resolved before it began
    assert client.post("/api/diagnoses/", json={"patient_id": 1, "name": " "}).status_code == 400
    future = (date.today() + timedelta(days=3)).isoformat()
    assert client.post("/api/diagnoses/", json={"patient_id": 1, "name": "X", "onset_date": future}).status_code == 400
    assert client.post("/api/diagnoses/", json={"patient_id": 1, "name": "X", "status": "resolved", "onset_date": "2024-02-02",
                                                 "resolved_date": "2024-01-01"}).status_code == 400
    assert client.post("/api/diagnoses/", json={"patient_id": 1, "name": "X", "status": "maybe"}).status_code == 422

    # Marking it resolved; an ongoing one never keeps a resolved date
    done = client.put(f"/api/diagnoses/{first['id']}", json={**first, "status": "resolved", "resolved_date": "2025-05-05"}).json()["data"]
    assert done["status"] == "resolved" and done["resolved_date"] == "2025-05-05"
    again = client.put(f"/api/diagnoses/{first['id']}", json={**done, "status": "active"}).json()["data"]
    assert again["resolved_date"] is None
    listed = client.get("/api/diagnoses/?patient_id=1").json()
    assert [d["status"] for d in listed] == ["active", "resolved"]  # ongoing first
    assert client.delete(f"/api/diagnoses/{first['id']}").status_code == 200
    assert client.get("/diagnoses").status_code == 200


def test_search(client):
    add(client)
    assert client.get("/api/search/?q=hypert").json()[0]["url"] == "/diagnoses"
    assert client.get("/api/search/?q=i10").json()[0]["label"] == "Essential hypertension"
    assert any(item["label"] == "Diagnoses" for item in client.get("/api/search/?q=problem list").json())


def test_backup_reset_and_related_records(client):
    add(client, provider_id=1)
    add(client, name="Acute sinusitis", icd10_code=None, status="resolved", onset_date=None, resolved_date="2023-01-20")
    backup = client.post("/api/settings/export", json={"patients": ["all"], "include_pdfs": False})
    assert len(backup.json()["diagnoses"]) == 2
    preview = client.post("/api/settings/import-preview", files={"import_file": ("b.json", backup.content, "application/json")}).json()
    assert preview["diagnoses_count"] == 2
    assert client.get("/api/settings/data-counts").json()["data"]["diagnoses"] == 2
    assert client.post("/api/settings/reset-data").status_code == 200
    assert client.get("/api/diagnoses/").json() == []
    r = client.post("/api/settings/import", files={"import_file": ("b.json", backup.content, "application/json")}, data={"merge_data": "true"})
    assert r.status_code == 200, r.text
    restored = sorted((d["name"], d["icd10_code"] or "", d["status"], d["onset_date"] or "", d["resolved_date"] or "", bool(d["provider_id"]))
                      for d in client.get("/api/diagnoses/").json())
    assert restored == [("Acute sinusitis", "", "resolved", "", "2023-01-20", False),
                        ("Essential hypertension", "I10", "active", "2024-03-01", "", True)]

    patient = client.post("/api/patients/", json={"name": "Alex"}).json()["data"]
    add(client, patient_id=patient["id"])
    assert client.delete(f"/api/patients/{patient['id']}").status_code == 400
    provider = client.post("/api/providers/", json={"name": "Dr Z"}).json()
    provider = provider.get("data", provider)
    item = add(client, name="Asthma", provider_id=provider["id"])
    assert client.delete(f"/api/providers/{provider['id']}").status_code == 200
    assert next(d for d in client.get("/api/diagnoses/").json() if d["id"] == item["id"])["provider_id"] is None


AI_READING = {
    "visit_date": VISIT, "ordering_provider": "Alex Doe", "tests": [],
    "diagnoses": [
        {"name": "Essential hypertension", "icd10_code": "I10", "context": "visit"},
        {"name": "High cholesterol", "matched_diagnosis": "Hyperlipidemia", "icd10_code": "E78.5", "context": "problem_list"},
        {"name": "Seasonal allergies", "icd10_code": "J30.2", "context": "problem_list", "status": "resolved", "resolved_date": "2025-06-01"},
        {"name": "Fractured wrist", "context": "history", "status": "resolved", "onset_date": "2010-05-01"},
        {"name": "Type 2 diabetes", "icd10_code": "E11.9 ", "context": "problem_list", "onset_date": "2021-02-03"},
        {"name": "Mild asthma", "icd10_code": "J45.20", "context": "problem_list"},
        {"name": " "},  # no name: left out
    ],
}


def setup_list(client):
    """Alex Doe as a provider, and three diagnoses already on the list (one resolved)."""
    client.post("/api/providers/", json={"name": "Alex Doe"})
    add(client, name="Hyperlipidemia", icd10_code="E78.5", onset_date="2022-01-01")
    add(client, name="Seasonal allergic rhinitis", icd10_code="J30.2", onset_date="2015-04-01")
    add(client, name="Asthma", icd10_code="J45.20", status="resolved", onset_date="2001-01-01", resolved_date="2012-01-01")


def test_review_and_save(client, ai):
    setup_list(client)
    ai.reply = AI_READING
    p = client.post("/api/pdf/upload", files={"file": ("visit.pdf", pdfs.visit_summary(tag="dx"), "application/pdf")}).json()
    sent = ai.requests[-1]["messages"][0]["content"][1]["text"]
    assert "The patient's diagnoses in the app" in sent and "- Hyperlipidemia" in sent
    assert "diagnoses" in ai.requests[-1]["tools"][0]["input_schema"]["properties"]

    rows = p["diagnoses"]
    assert [(d["name"], d["plan"]) for d in rows] == [
        ("Essential hypertension", "add"),        # new
        ("High cholesterol", "none"),             # the AI matched it to Hyperlipidemia
        ("Seasonal allergies", "resolve"),        # same ICD-10 code, marked resolved on the document
        ("Fractured wrist", "add_resolved"),      # past history
        ("Type 2 diabetes", "add"),
        ("Mild asthma", "none"),                  # recorded as resolved; not reopened by a document
    ]
    assert rows[0]["icd10_code"] == "I10" and rows[4]["icd10_code"] == "E11.9"
    assert "Hyperlipidemia" in rows[1]["note"] and "Diagnoses page" in rows[5]["note"]

    r = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [{
        "import_id": p["import_id"], "selected_tests": [], "provider_id": p["matched_provider"]["id"],
        "selected_diagnoses": [0, 1, 2, 3, 4, 5]}]})
    data = r.json()["data"]
    assert r.status_code == 200 and data["failed_count"] == 0, r.text
    assert [c["plan"] for c in data["files"][0]["diagnosis_changes"]] == ["add", "resolve", "add_resolved", "add"]

    saved = {d["name"]: d for d in client.get("/api/diagnoses/?patient_id=1").json()}
    assert len(saved) == 6
    htn = saved["Essential hypertension"]
    assert (htn["status"], htn["onset_date"], htn["icd10_code"], htn["provider_name"]) == ("active", VISIT, "I10", "Alex Doe")
    assert saved["Seasonal allergic rhinitis"]["status"] == "resolved" and saved["Seasonal allergic rhinitis"]["resolved_date"] == "2025-06-01"
    wrist = saved["Fractured wrist"]
    assert (wrist["status"], wrist["onset_date"], wrist["resolved_date"]) == ("resolved", "2010-05-01", None)
    assert saved["Type 2 diabetes"]["onset_date"] == "2021-02-03"  # the printed date wins over the visit's

    # Saved rows are marked; confirming again changes nothing; the import can't be re-read
    again = client.get(f"/api/pdf/review/{p['import_id']}").json()["diagnoses"]
    assert [d["already_imported"] for d in again] == [True, False, True, True, True, False]
    r = client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [], "provider_id": 1,
                                               "selected_diagnoses": [0, 2, 3, 4]})
    assert r.status_code == 200 and r.json()["data"]["diagnosis_changes"] == []
    assert len(client.get("/api/diagnoses/?patient_id=1").json()) == 6
    assert client.post(f"/api/pdf/rescan-ai/{p['import_id']}").status_code == 400

    # The same visit summary, uploaded as another file, finds them all recorded
    ai.reply = AI_READING
    p2 = client.post("/api/pdf/upload", files={"file": ("again.pdf", pdfs.visit_summary(tag="dx again"), "application/pdf")}).json()
    assert [d["plan"] for d in p2["diagnoses"]] == ["none"] * 6


def test_diagnoses_without_a_visit_date(client, ai):
    """A records export with no visit date: diagnoses are saved without an onset date rather than refused."""
    ai.reply = {"tests": [], "diagnoses": [{"name": "Migraine", "icd10_code": "G43.909", "context": "problem_list"}]}
    p = client.post("/api/pdf/upload", files={"file": ("records.pdf", pdfs.visit_summary(tag="no date dx"), "application/pdf")}).json()
    assert p["date_collected"] is None
    r = client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [], "provider_id": 1,
                                               "selected_diagnoses": [0]})
    assert r.status_code == 200, r.text
    assert "1 diagnosis change" in r.json()["message"]
    assert client.get("/api/diagnoses/").json()[0]["onset_date"] is None


def test_icd10_codes_are_cleaned():
    from api.services.diagnosis_review import _code
    assert _code(" e78.5 ") == "E78.5" and _code("I10.") == "I10" and _code("Z00.00") == "Z00.00"
    assert _code("not a code") is None and _code(None) is None and _code("1234") is None
