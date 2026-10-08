"""Setup, vitals and units, backups, and logging."""

import logging

from api.routers.vitals import convert, format_reading


def test_first_run_setup(fresh_client):
    page = fresh_client.get("/dashboard", follow_redirects=False)
    assert page.status_code in (302, 303, 307) and "/welcome" in page.headers["location"]
    r = fresh_client.post("/api/setup/", json={"name": "Sal"})
    assert r.status_code == 200 and r.json()["patient"]["name"] == "Sal"
    assert "set-cookie" not in r.headers  # the welcome page stores the patient choice in the browser
    assert fresh_client.get("/dashboard").status_code == 200


def test_vital_formatting():
    assert format_reading("weight", 70, None, "kg", "lb") == "154.3 lb"
    assert format_reading("height", 177.8, None, "cm", "in") == "5′ 10″"
    assert format_reading("height", 71.7, None, "in", "in") == "6′ 0″"
    assert format_reading("temperature", 98.6, None, "°F", "°C") == "37 °C"
    assert format_reading("blood_glucose", 90, None, "mg/dL", "mmol/L") == "5 mmol/L"
    assert format_reading("blood_pressure", 120, 80, "mmHg", "mmHg") == "120/80 mmHg"
    assert abs(convert("weight", convert("weight", 70, "kg", "lb"), "lb", "kg") - 70) < 1e-9


def test_vital_unit_preferences(client):
    types = client.get("/api/vitals/types").json()
    assert [types[t]["preferred_unit"] for t in ("weight", "height", "temperature", "blood_glucose")] == ["lb", "in", "°F", "mg/dL"]
    client.post("/api/vitals/", json={"patient_id": 1, "vital_type": "weight", "value": 70, "unit": "kg", "measured_at": "2026-01-01T08:00"})
    client.post("/api/vitals/", json={"patient_id": 1, "vital_type": "temperature", "value": 98.6, "unit": "°F", "measured_at": "2026-01-01T08:00"})
    assert "154.3 lb" in client.get("/dashboard").text

    assert client.put("/api/settings/user", json={"vital_units": {"weight": "stone"}}).status_code == 400
    assert client.put("/api/settings/user", json={"vital_units": {"shoe_size": "eu"}}).status_code == 400
    metric = {"weight": "kg", "height": "cm", "temperature": "°C", "blood_glucose": "mmol/L"}
    assert client.put("/api/settings/user", json={"vital_units": metric}).status_code == 200
    client.put("/api/settings/user", json={"dark_mode": True})  # saving another setting keeps the units
    assert client.get("/api/vitals/types").json()["weight"]["preferred_unit"] == "kg"
    page = client.get("/dashboard").text
    assert "70 kg" in page and "37 °C" in page
    stored = sorted((v["value"], v["unit"]) for v in client.get("/api/vitals/?patient_id=1").json())
    assert stored == [(70.0, "kg"), (98.6, "°F")]


def test_backup_round_trip(client):
    client.post("/api/panels/", json={"name": "Chemistry"})
    client.post("/api/units/", json={"name": "mg/dL"})
    client.post("/api/labs/", json={"name": "Glucose", "panel_id": 1, "unit_id": 1})
    client.post("/api/results/", json={"lab_id": 1, "patient_id": 1, "provider_id": 1, "result": 88, "date_collected": "2025-01-02"})
    client.post("/api/vitals/", json={"patient_id": 1, "vital_type": "weight", "value": 150, "unit": "lb", "measured_at": "2025-01-02T08:00"})
    backup = client.post("/api/settings/export", json={"patients": ["all"], "include_pdfs": False})
    assert backup.status_code == 200

    assert client.post("/api/settings/reset-data").status_code == 200
    assert client.get("/api/results/").json()["total_count"] == 0
    r = client.post("/api/settings/import", files={"import_file": ("backup.json", backup.content, "application/json")},
                    data={"merge_data": "true"})
    assert r.status_code == 200, r.text
    results = client.get("/api/results/").json()["results"]
    assert [(x["lab"]["name"], x["result"]) for x in results] == [("Glucose", 88.0)]
    assert [(v["value"], v["unit"]) for v in client.get("/api/vitals/?patient_id=1").json()] == [(150.0, "lb")]


def test_logging(client, caplog):
    caplog.set_level(logging.INFO)
    client.post("/api/results/", json={"lab_id": 1, "patient_id": 1, "provider_id": 1, "result": "SECRET-VALUE", "date_collected": "2025-01-01"})
    client.get("/api/search/?q=testosterone")
    client.put("/api/providers/1", json={"name": "Dr B"})
    response = client.get("/api/pdf/review/999")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET-VALUE" not in text and "testosterone" not in text  # no submitted values or search terms
    assert "body.result" in text                                         # but the field and reason are
    assert "api.change method=PUT route=/api/providers/{provider_id}" in text
    assert "GET /api/pdf/review/{import_id} 404" in text
    assert response.headers.get("x-request-id")
