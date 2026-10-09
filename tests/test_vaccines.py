"""Vaccines: doses per patient, next-dose due dates, and where they appear."""

from datetime import date, timedelta


def add(client, **fields):
    body = {"patient_id": 1, "vaccine": "COVID-19", "date_given": "2025-10-01", "dose": "1 of 2", "manufacturer": "Pfizer", **fields}
    response = client.post("/api/vaccines/", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def soon(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_doses_and_due_status(client):
    first = add(client, lot_number="AB123", site="left arm", provider_id=1, location="  ", next_due=soon(-3))
    assert first["provider_name"] and first["location"] is None and first["due_status"] == "overdue"
    assert add(client, vaccine="Shingles (zoster)", next_due=soon(10))["due_status"] == "due"
    assert add(client, vaccine="Tdap (tetanus, diphtheria, pertussis)", next_due=soon(3650))["due_status"] == "scheduled"
    assert add(client, vaccine="Influenza (flu)")["due_status"] is None

    # Validation: a name, a date that has happened, and a next dose after this one
    assert client.post("/api/vaccines/", json={"patient_id": 1, "vaccine": " ", "date_given": "2025-01-01"}).status_code == 400
    assert client.post("/api/vaccines/", json={"patient_id": 1, "vaccine": "X", "date_given": soon(5)}).status_code == 400
    assert client.post("/api/vaccines/", json={"patient_id": 1, "vaccine": "X", "date_given": "2025-01-01",
                                               "next_due": "2025-01-01"}).status_code == 400

    second = client.put(f"/api/vaccines/{first['id']}", json={"patient_id": 1, "vaccine": "COVID-19", "date_given": "2025-10-01",
                                                                "dose": "1 of 2", "next_due": None}).json()["data"]
    assert second["due_status"] is None and second["lot_number"] is None
    listed = client.get("/api/vaccines/?patient_id=1").json()
    assert [v["date_given"] for v in listed] == sorted((v["date_given"] for v in listed), reverse=True)
    options = client.get("/api/vaccines/options?patient_id=1").json()
    assert options["vaccines"][0] == "COVID-19" and "RSV" in options["vaccines"] and "left arm" in options["sites"]
    assert client.delete(f"/api/vaccines/{first['id']}").status_code == 200


def test_dashboard_search_and_page(client):
    add(client, next_due=soon(-1))                                 # overdue, but superseded by the next dose below
    add(client, date_given=date.today().isoformat(), dose="2 of 2")
    add(client, vaccine="Shingles (zoster)", next_due=soon(14))    # due soon
    add(client, vaccine="Tdap (tetanus, diphtheria, pertussis)", next_due=soon(3000))  # not soon
    page = client.get("/dashboard").text
    assert "Vaccines due" in page and "Shingles (zoster)" in page and "Due" in page
    assert "Tdap" not in page and "Overdue" not in page              # only the latest COVID-19 dose counts
    found = client.get("/api/search/?q=shingles").json()
    assert found[0]["label"] == "Shingles (zoster)" and found[0]["url"] == "/vaccines"
    assert any(item["label"] == "Vaccines" for item in client.get("/api/search/?q=immunizations").json())
    assert client.get("/vaccines").status_code == 200


def test_no_dashboard_card_when_nothing_is_due(client):
    add(client)
    assert "Vaccines due" not in client.get("/dashboard").text


def test_backup_reset_and_related_records(client):
    add(client, lot_number="AB123", provider_id=1, next_due="2026-04-01")
    add(client, vaccine="Influenza (flu)", location="CVS Pharmacy")
    backup = client.post("/api/settings/export", json={"patients": ["all"], "include_pdfs": False})
    assert len(backup.json()["immunizations"]) == 2
    preview = client.post("/api/settings/import-preview", files={"import_file": ("b.json", backup.content, "application/json")}).json()
    assert preview["immunizations_count"] == 2
    assert client.get("/api/settings/data-counts").json()["data"]["immunizations"] == 2
    assert client.post("/api/settings/reset-data").status_code == 200
    assert client.get("/api/vaccines/").json() == []
    r = client.post("/api/settings/import", files={"import_file": ("b.json", backup.content, "application/json")}, data={"merge_data": "true"})
    assert r.status_code == 200, r.text
    restored = sorted((v["vaccine"], v["lot_number"] or "", v["next_due"] or "", v["location"] or "", bool(v["provider_id"]))
                      for v in client.get("/api/vaccines/").json())
    assert restored == [("COVID-19", "AB123", "2026-04-01", "", True), ("Influenza (flu)", "", "", "CVS Pharmacy", False)]

    patient = client.post("/api/patients/", json={"name": "Alex"}).json()["data"]
    add(client, patient_id=patient["id"])
    assert client.delete(f"/api/patients/{patient['id']}").status_code == 400
    provider = client.post("/api/providers/", json={"name": "Dr Z"}).json()
    provider = provider.get("data", provider)
    shot = add(client, vaccine="RSV", provider_id=provider["id"])
    assert client.delete(f"/api/providers/{provider['id']}").status_code == 200
    assert next(v for v in client.get("/api/vaccines/").json() if v["id"] == shot["id"])["provider_id"] is None


def test_merging_providers_moves_given_by(client):
    client.post("/api/providers/", json={"name": "Dr Jane Smith"})
    add(client, provider_id=2)
    assert client.post("/api/cleanup/merge", json={"merges": [{"kind": "providers", "keep_id": 1, "merge_ids": [2]}]}).status_code == 200
    assert client.get("/api/vaccines/").json()[0]["provider_id"] == 1
