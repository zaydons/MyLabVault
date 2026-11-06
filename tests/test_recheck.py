"""Re-checking an imported report with AI (Bedrock mocked): compare with what's saved, correct and re-add."""

import pdfs

REPORT = pdfs.lab_report([["Cholesterol, Total", "185", "", "mg/dL", "100-199"], ["Triglycerides", "210", "High", "mg/dL", "0-149"],
                          ["Glucose", "88", "", "mg/dL", "70-99"]], tag="recheck")
AI_READING = {"collection_date": "2026-01-15", "tests": [
    {"name": "Cholesterol, Total", "value": 185, "unit": "mg/dL", "ref_text": "100-199"},
    {"name": "Triglycerides", "value": 201, "unit": "mg/dL", "ref_low": 0, "ref_high": 149, "ref_text": "0-149", "flag": "H"},
    {"name": "Glucose", "value": 88, "unit": "mg/dL", "ref_low": 70, "ref_high": 99, "ref_text": "70-99"},
    {"name": "HDL Cholesterol", "value": 52, "unit": "mg/dL", "ref_low": 39, "ref_text": ">39"},
]}


def imported(client):
    """The report imported with the built-in reader, then its Glucose result deleted."""
    p = client.post("/api/pdf/upload", files={"file": ("r.pdf", REPORT, "application/pdf")}).json()
    assert p["parser"] == "standard" and len(p["tests"]) == 3
    saved = client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [0, 1, 2], "provider_id": 1})
    assert saved.status_code == 200, saved.text
    results = client.get("/api/results/").json()["results"]
    glucose = next(r for r in results if r["lab"]["name"] == "Glucose")
    assert client.delete(f"/api/results/{glucose['id']}").status_code == 200
    return p["import_id"]


def test_recheck_needs_ai(client):
    iid = imported(client)
    assert client.post(f"/api/pdf/{iid}/recheck").status_code == 400
    assert client.get(f"/api/pdf/{iid}/recheck").status_code == 404


def test_recheck_compare_and_apply(client, ai):
    iid = imported(client)
    ai.reply = AI_READING
    c = client.post(f"/api/pdf/{iid}/recheck").json()
    rows = {r["name"]: r for r in c["rows"]}
    assert c["summary"] == {"same": 1, "different": 1, "missing": 2, "only_saved": 0}
    assert rows["Triglycerides"]["status"] == "different" and rows["Triglycerides"]["saved_value"] == "210" and rows["Triglycerides"]["ai_value"] == "201"
    assert rows["Glucose"]["status"] == "missing" and rows["Glucose"]["lab_name"] == "Glucose"  # deleted, can be added back
    assert rows["HDL Cholesterol"]["status"] == "missing" and rows["HDL Cholesterol"]["lab_name"] is None
    # The original reading is kept: the review still marks the two results that are still saved
    review = client.get(f"/api/pdf/review/{iid}").json()
    assert review["parser"] == "standard" and [t["already_imported"] for t in review["tests"]] == [True, True, False]

    r = client.post(f"/api/pdf/{iid}/recheck/apply", json={"indices": [1, 2]})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["updated"] == [{"name": "Triglycerides", "from": "210", "to": "201"}]
    assert data["added"] == [{"name": "Glucose", "value": "88", "date": "2026-01-15"}]
    assert data["comparison"]["summary"] == {"same": 3, "different": 0, "missing": 1, "only_saved": 0}

    results = {r["lab"]["name"]: r for r in client.get("/api/results/").json()["results"]}
    assert results["Triglycerides"]["result"] == 201 and results["Triglycerides"]["flag"] == "H"
    assert results["Glucose"]["result"] == 88 and results["Glucose"]["date_collected"].startswith("2026-01-15")
    assert results["Glucose"]["provider_id"] == 1  # the import's provider
    history = next(h for h in client.get("/api/pdf/history").json() if str(h["id"]) == str(iid))
    assert history["results_saved"] == 3

    # Applying the same rows again changes nothing; the last re-check can be reopened
    again = client.post(f"/api/pdf/{iid}/recheck/apply", json={"indices": [1, 2]}).json()["data"]
    assert again["updated"] == [] and again["added"] == []
    assert client.get(f"/api/pdf/{iid}/recheck").json()["summary"]["missing"] == 1

    # A new test is created for a result the app has never seen
    assert client.post(f"/api/pdf/{iid}/recheck/apply", json={"indices": [3]}).json()["data"]["added"][0]["name"] == "HDL Cholesterol"


def test_only_saved_and_hand_entries(client, ai):
    iid = imported(client)
    ai.reply = {"collection_date": "2026-01-15", "tests": [AI_READING["tests"][0]]}
    c = client.post(f"/api/pdf/{iid}/recheck").json()
    assert c["summary"]["only_saved"] == 1 and c["only_saved"][0]["name"] == "Triglycerides"

    manual = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [{
        "key": 1, "manual": {"tests": [{"name": "Glucose", "result": "90", "unit": "mg/dL"}]}, "selected_tests": [0],
        "provider_id": 1, "manual_date": "2026-02-01"}]}).json()["data"]["files"][0]
    r = client.post(f"/api/pdf/{manual['import_id']}/recheck")
    assert r.status_code == 400 and "no PDF" in r.json()["detail"]
