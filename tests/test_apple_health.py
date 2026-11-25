"""Apple Health: vitals offered to an iPhone Shortcut, behind a token, sent once each."""

GROUPS = ["Weight", "Height", "Blood Pressure", "Heart Rate", "Body Temperature", "Oxygen Saturation",
          "Respiratory Rate", "Blood Glucose"]


def vital(client, vital_type, value, unit, when, value2=None, patient_id=1):
    body = {"patient_id": patient_id, "vital_type": vital_type, "value": value, "unit": unit, "measured_at": when}
    if value2 is not None:
        body["value2"] = value2
    r = client.post("/api/vitals/", json=body)
    assert r.status_code == 200, r.text


def token(client, patient_id=1):
    r = client.post("/api/apple-health/token", json={"patient_id": patient_id})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_needs_a_token(client):
    assert client.get("/api/apple-health/vitals").status_code == 401  # not set up
    assert client.get("/api/apple-health/").json()["enabled"] is False
    t = token(client)
    assert client.get("/api/apple-health/vitals").status_code == 401
    assert client.get("/api/apple-health/vitals", headers=auth("wrong")).status_code == 401
    assert client.get("/api/apple-health/vitals", headers={"Authorization": t}).status_code == 401  # no "Bearer"
    assert client.get("/api/apple-health/vitals", headers=auth(t)).status_code == 200
    assert client.post("/api/apple-health/vitals/sent", json={"through": 1}).status_code == 401

    # A new token replaces the old one; turning it off stops both
    t2 = token(client)
    assert client.get("/api/apple-health/vitals", headers=auth(t)).status_code == 401
    assert client.delete("/api/apple-health/token").json()["enabled"] is False
    assert client.get("/api/apple-health/vitals", headers=auth(t2)).status_code == 401
    assert client.post("/api/apple-health/token", json={"patient_id": 999}).status_code == 400


def test_token_hash_is_never_shown_or_overwritten(client):
    t = token(client)
    shown = client.get("/api/settings/user").text
    assert "apple_health" not in shown and "token_hash" not in shown
    client.put("/api/settings/user", json={"dark_mode": True, "apple_health": {"token_hash": "x"}})
    assert client.get("/api/apple-health/vitals", headers=auth(t)).status_code == 200
    assert t not in client.get("/api/apple-health/").text


def test_grouped_in_fixed_units_and_sent_once(client):
    t = token(client)
    vital(client, "weight", 81.6, "kg", "2026-10-01T08:00:00")
    vital(client, "blood_pressure", 128, "mmHg", "2026-10-01T08:05:00", value2=82)
    vital(client, "temperature", 37, "°C", "2026-10-02T09:30:00")
    vital(client, "blood_glucose", 5.5, "mmol/L", "2026-10-03T07:00:00")
    vital(client, "heart_rate", 64, "bpm", "2026-10-03T07:01:00")
    vital(client, "oxygen_saturation", 97, "%", "2026-10-03T07:02:00")
    vital(client, "weight", 70, "kg", "2026-10-03T07:00:00", patient_id=client.post("/api/patients/", json={"name": "Alex"}).json()["data"]["id"])

    data = client.get("/api/apple-health/vitals", headers=auth(t)).json()
    assert all(group in data for group in GROUPS) and data["count"] == 6 and not data["more"]
    assert data["Weight"] == [{"value": 179.9, "unit": "lb", "date": "2026-10-01T08:00"}]  # only this patient's
    assert data["Blood Pressure"] == [{"systolic": 128, "diastolic": 82, "unit": "mmHg", "date": "2026-10-01T08:05"}]
    assert data["Body Temperature"][0] == {"value": 98.6, "unit": "°F", "date": "2026-10-02T09:30"}
    assert data["Blood Glucose"][0]["value"] == 99.0 and data["Heart Rate"][0]["value"] == 64
    assert data["Oxygen Saturation"][0] == {"value": 97, "unit": "%", "date": "2026-10-03T07:02"}
    assert data["Height"] == [] and data["Respiratory Rate"] == []

    metric = client.get("/api/apple-health/vitals", headers=auth(t), params={"units": "metric"}).json()
    assert metric["Weight"][0] == {"value": 81.6, "unit": "kg", "date": "2026-10-01T08:00"}
    assert metric["Body Temperature"][0]["unit"] == "°C" and metric["Blood Glucose"][0]["value"] == 5.5

    # Confirmed readings aren't sent again; a later reading (even for an earlier date) is
    assert client.get("/api/apple-health/").json()["pending"] == 6
    r = client.post("/api/apple-health/vitals/sent", headers=auth(t), json={"through": data["through"]})
    assert r.status_code == 200 and r.json()["pending"] == 0 and r.json()["last_sync"]
    assert client.get("/api/apple-health/vitals", headers=auth(t)).json()["count"] == 0
    vital(client, "weight", 178, "lb", "2026-09-01T08:00:00")
    later = client.get("/api/apple-health/vitals", headers=auth(t)).json()
    assert later["count"] == 1 and later["Weight"][0]["date"] == "2026-09-01T08:00"
    # A marker can't skip readings that don't exist yet, or go backwards
    client.post("/api/apple-health/vitals/sent", headers=auth(t), json={"through": 10 ** 9})
    vital(client, "weight", 177, "lb", "2026-11-01T08:00:00")
    assert client.get("/api/apple-health/vitals", headers=auth(t)).json()["count"] == 1
    client.post("/api/apple-health/vitals/sent", headers=auth(t), json={"through": 0})
    assert client.get("/api/apple-health/vitals", headers=auth(t)).json()["count"] == 1

    # Send everything again, or count everything as sent
    assert client.post("/api/apple-health/resend").json()["pending"] == 8
    assert client.post("/api/apple-health/mark-sent").json()["pending"] == 0
    status = client.get("/api/apple-health/").json()
    assert status["enabled"] and status["patient_name"] and status["last_sync"]


def test_batches(client, monkeypatch):
    from api.routers import apple_health
    monkeypatch.setattr(apple_health, "BATCH", 2)
    t = token(client)
    for day in range(1, 6):
        vital(client, "heart_rate", 60 + day, "bpm", f"2026-10-0{day}T08:00:00")
    first = client.get("/api/apple-health/vitals", headers=auth(t)).json()
    assert first["count"] == 2 and first["more"]
    client.post("/api/apple-health/vitals/sent", headers=auth(t), json={"through": first["through"]})
    second = client.get("/api/apple-health/vitals", headers=auth(t)).json()
    assert [v["value"] for v in second["Heart Rate"]] == [63, 64] and second["more"]


def test_reset_turns_it_off(client):
    t = token(client)
    assert client.post("/api/settings/reset-data").status_code == 200
    assert client.get("/api/apple-health/vitals", headers=auth(t)).status_code == 401
