"""Visits: results grouped by the day they were collected, with range bars and previous results."""

from api.services.visits import range_scale


def setup_labs(client):
    client.post("/api/panels/", json={"name": "Lipid"})
    client.post("/api/panels/", json={"name": "Chemistry"})
    client.post("/api/units/", json={"name": "mg/dL"})
    trig = client.post("/api/labs/", json={"name": "Triglycerides", "panel_id": 1, "unit_id": 1, "ref_low": 0, "ref_high": 150}).json()["data"]
    hdl = client.post("/api/labs/", json={"name": "HDL", "panel_id": 1, "unit_id": 1, "ref_type": "greater", "ref_value": 39}).json()["data"]
    glucose = client.post("/api/labs/", json={"name": "Glucose", "panel_id": 2, "unit_id": 1, "ref_low": 70, "ref_high": 99}).json()["data"]
    return trig["id"], hdl["id"], glucose["id"]


def add(client, lab_id, value, when, **fields):
    body = {"lab_id": lab_id, "patient_id": 1, "provider_id": 1, "result": value, "date_collected": when, **fields}
    response = client.post("/api/results/", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_range_scale():
    assert range_scale(None, 0, 150) is None and range_scale(5, None, None) is None and range_scale(5, 10, 10) is None
    inside = range_scale(100, 70, 130)          # bar runs 40-160
    assert inside == {"low": 25.0, "high": 75.0, "value": 50.0}
    high = range_scale(410, 0, 150)             # far above: the bar widens so the marker stays on it
    assert high["value"] > high["high"] and 90 < high["value"] < 100
    low = range_scale(79.2, 82.6, 95.8)
    assert low["value"] < low["low"]
    above_only = range_scale(103, 60, None)     # eGFR > 60: open-ended to the right
    assert above_only["high"] == 100.0 and above_only["low"] < above_only["value"] < 100
    below_only = range_scale(110, None, 100)
    assert below_only["low"] == 0.0 and below_only["value"] > below_only["high"]


def test_visit_list_and_page(client):
    trig, hdl, glucose = setup_labs(client)
    add(client, trig, 204, "2025-10-13T08:00:00")
    add(client, hdl, 54, "2025-10-13T08:00:00")
    add(client, trig, 410, "2026-10-05T08:00:00", fasting=True, lab_comment="Lipemic specimen")
    add(client, hdl, 40, "2026-10-05T08:00:00")
    add(client, glucose, 88, "2026-10-05T09:30:00")      # same day, later: same visit

    page = client.get("/visits").text
    assert page.index("Oct 5, 2026") < page.index("Oct 13, 2025")
    assert "/visits/2026-10-05" in page and "1 out of range" in page and "Chemistry, Lipid" in page

    page = client.get("/visits/2026-10-05").text
    assert "Visit: Oct 5, 2026" in page and "3 results" in page and "1 high" in page and "2 normal" in page
    assert page.index("Chemistry") < page.index("Lipid")                 # panels in order
    assert "Lipemic specimen" in page and "Fasting" in page
    assert "410 <span" in page and "above the range" in page and "within the range" in page
    assert "on Oct 13, 2025" in page and "up 206" in page and "down 14" in page   # previous results and change
    assert 'href="/visits/2025-10-13"' in page and "Older: Oct 13, 2025" in page and "Newer:" not in page

    older = client.get("/visits/2025-10-13").text
    assert "Newer: Oct 5, 2026" in older and "Older:" not in older
    assert client.get("/visits/2024-01-01").status_code == 404
    assert client.get("/visits/not-a-date").status_code == 422


def test_visit_links(client):
    trig, _, _ = setup_labs(client)
    assert "No results yet" in client.get("/visits").text
    add(client, trig, 120, "2026-10-05T08:00:00")
    assert 'href="/visits/2026-10-05"' in client.get("/dashboard").text
    assert 'href="/visits"' in client.get("/results").text
    assert any(item["url"] == "/visits" for item in client.get("/api/search/?q=visits").json())


def test_other_patients_results_stay_out(client):
    trig, _, _ = setup_labs(client)
    other = client.post("/api/patients/", json={"name": "Someone else"}).json()["data"]
    client.post("/api/results/", json={"lab_id": trig, "patient_id": other["id"], "provider_id": 1, "result": 300,
                                       "date_collected": "2026-10-05T08:00:00"})
    assert client.get("/visits/2026-10-05").status_code == 404
    client.cookies.set("selectedPatientId", str(other["id"]))
    assert "300" in client.get("/visits/2026-10-05").text
