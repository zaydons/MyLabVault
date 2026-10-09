"""Medications and supplements: dose periods, dose changes, stopping, and where they appear."""


def add(client, **fields):
    body = {"patient_id": 1, "name": "Testosterone cypionate", "dose": "100 mg", "frequency": "weekly",
            "route": "injection", "start_date": "2026-01-05", **fields}
    response = client.post("/api/medications/", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_dose_changes_and_stopping(client):
    med = add(client, reason="low testosterone", provider_id=1)
    assert med["current"] and med["end_date"] is None and med["provider_name"]

    changed = client.post(f"/api/medications/{med['id']}/change-dose", json={"dose": "140 mg", "change_date": "2026-06-03"}).json()["data"]
    assert changed["previous"]["end_date"] == "2026-06-02" and not changed["previous"]["current"]
    new = changed["current"]
    assert (new["dose"], new["frequency"], new["route"], new["start_date"], new["reason"]) == \
        ("140 mg", "weekly", "injection", "2026-06-03", "low testosterone")

    # A change has to be new and to come after the dose it replaces
    assert client.post(f"/api/medications/{new['id']}/change-dose", json={"dose": "140 mg", "change_date": "2026-07-01"}).status_code == 400
    assert client.post(f"/api/medications/{new['id']}/change-dose", json={"dose": "160 mg", "change_date": "2026-06-03"}).status_code == 400

    current = client.get("/api/medications/?patient_id=1&status=current").json()
    assert [(m["name"], m["dose"]) for m in current] == [("Testosterone cypionate", "140 mg")]
    assert client.post(f"/api/medications/{new['id']}/stop", json={"end_date": "2026-01-01"}).status_code == 400
    stopped = client.post(f"/api/medications/{new['id']}/stop", json={"end_date": "2026-09-30"}).json()["data"]
    assert stopped["end_date"] == "2026-09-30" and not stopped["current"]
    history = client.get("/api/medications/?patient_id=1&status=past").json()
    assert [m["dose"] for m in history] == ["140 mg", "100 mg"]  # newest first


def test_validation_and_supplements(client):
    assert client.post("/api/medications/", json={"patient_id": 1, "name": " ", "start_date": "2026-01-01"}).status_code == 400
    assert client.post("/api/medications/", json={"patient_id": 1, "name": "X", "start_date": "2026-02-01",
                                                  "end_date": "2026-01-01"}).status_code == 400
    assert client.post("/api/medications/", json={"patient_id": 1, "name": "X", "kind": "vitamin", "start_date": "2026-01-01"}).status_code == 422
    assert client.post("/api/medications/", json={"patient_id": 99, "name": "X", "start_date": "2026-01-01"}).status_code == 400
    vit = add(client, name="Vitamin D3", kind="supplement", dose="2000 IU", frequency="once daily", route=" ")
    assert vit["kind"] == "supplement" and vit["route"] is None
    planned_stop = add(client, name="Prednisone", start_date="2026-01-01", end_date="2999-01-01")
    assert planned_stop["current"]  # a stop date still ahead counts as current
    options = client.get("/api/medications/options?patient_id=1").json()
    assert {"Vitamin D3", "Prednisone"} <= set(options["names"]) and "as needed" in options["frequencies"]


def test_shown_on_dashboard_and_in_search(client):
    add(client)
    add(client, name="Vitamin D3", kind="supplement", dose="2000 IU")
    add(client, name="Atorvastatin", start_date="2023-03-01", end_date="2025-01-15")
    page = client.get("/dashboard").text
    assert "Current medications" in page and "Testosterone cypionate" in page and "Vitamin D3" in page
    assert "Atorvastatin" not in page  # stopped
    found = client.get("/api/search/?q=atorva").json()
    assert found[0]["label"] == "Atorvastatin" and "stopped" in found[0]["detail"] and found[0]["url"] == "/medications"
    assert any(item["label"] == "Medications" for item in client.get("/api/search/?q=supplements").json())
    assert client.get("/medications").status_code == 200


def test_backup_reset_and_related_records(client):
    med = add(client, provider_id=1)
    client.post(f"/api/medications/{med['id']}/change-dose", json={"dose": "140 mg", "change_date": "2026-06-03"})
    add(client, name="Vitamin D3", kind="supplement")
    backup = client.post("/api/settings/export", json={"patients": ["all"], "include_pdfs": False})
    assert len(backup.json()["medications"]) == 3

    preview = client.post("/api/settings/import-preview", files={"import_file": ("b.json", backup.content, "application/json")}).json()
    assert preview["medications_count"] == 3
    assert client.post("/api/settings/reset-data").status_code == 200
    assert client.get("/api/medications/").json() == []
    r = client.post("/api/settings/import", files={"import_file": ("b.json", backup.content, "application/json")}, data={"merge_data": "true"})
    assert r.status_code == 200, r.text
    restored = sorted((m["name"], m["dose"] or "", m["start_date"], m["end_date"] or "", m["kind"]) for m in client.get("/api/medications/").json())
    assert restored == [("Testosterone cypionate", "100 mg", "2026-01-05", "2026-06-02", "medication"),
                        ("Testosterone cypionate", "140 mg", "2026-06-03", "", "medication"),
                        ("Vitamin D3", "100 mg", "2026-01-05", "", "supplement")]
    assert all(m["provider_name"] for m in client.get("/api/medications/").json() if m["name"].startswith("Testosterone"))

    # A patient with medications isn't deleted; a deleted provider is just cleared as prescriber
    patient = client.post("/api/patients/", json={"name": "Alex"}).json()["data"]
    add(client, patient_id=patient["id"], name="Aspirin", provider_id=None)
    assert client.delete(f"/api/patients/{patient['id']}").status_code == 400
    provider = client.post("/api/providers/", json={"name": "Dr Z"}).json()
    provider = provider.get("data", provider)
    m = add(client, name="Lisinopril", provider_id=provider["id"])
    assert client.delete(f"/api/providers/{provider['id']}").status_code == 200
    assert next(x for x in client.get("/api/medications/").json() if x["id"] == m["id"])["provider_id"] is None


def test_merging_providers_moves_prescriber(client):
    client.post("/api/providers/", json={"name": "Dr Jane Smith"})
    med = add(client, provider_id=2)
    r = client.post("/api/cleanup/merge", json={"merges": [{"kind": "providers", "keep_id": 1, "merge_ids": [2]}]})
    assert r.status_code == 200, r.text
    assert client.get("/api/medications/").json()[0]["provider_id"] == 1 == med["provider_id"] - 1
