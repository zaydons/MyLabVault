"""Vaccines read with AI (Bedrock mocked) from an immunization record or a visit summary."""

from datetime import date, timedelta

import pdfs

FLU = (date.today() - timedelta(days=60)).isoformat()
COVID = (date.today() - timedelta(days=200)).isoformat()
SOON = (date.today() + timedelta(days=10)).isoformat()
RECORD = pdfs.immunization_record()
AI_READING = {
    "tests": [], "vitals": [], "medications": [],
    "immunizations": [
        {"vaccine": "COVID-19 mRNA (Pfizer)", "matched_vaccine": "COVID-19", "date_given": COVID, "dose": "Booster",
         "manufacturer": "Pfizer", "lot_number": "FA1234", "site": "left arm", "location": "Example Pharmacy"},
        {"vaccine": "Influenza, quadrivalent", "matched_vaccine": "Influenza (flu)", "date_given": FLU,
         "manufacturer": "Sanofi", "lot_number": "UT5678", "site": "right arm"},
        {"vaccine": "Tdap", "matched_vaccine": "Tdap (tetanus, diphtheria, pertussis)", "date_given": None},
        {"vaccine": "Shingrix", "matched_vaccine": "Not a name we sent", "date_given": FLU, "dose": "1 of 2", "next_due": SOON},
        {"vaccine": "  "},  # no name: left out
    ],
}


def setup(client):
    client.post("/api/providers/", json={"name": "Example Pharmacy"})
    # The flu shot is already on the Vaccines page, under the matched name
    assert client.post("/api/vaccines/", json={"patient_id": 1, "vaccine": "Influenza (flu)", "date_given": FLU}).status_code == 200


def test_review_and_save(client, ai):
    setup(client)
    ai.reply = AI_READING
    p = client.post("/api/pdf/upload", files={"file": ("shots.pdf", RECORD, "application/pdf")}, params={"ai": "true"}).json()
    sent = ai.requests[-1]["messages"][0]["content"][1]["text"]
    assert "Vaccine names in the app" in sent and "- Influenza (flu)" in sent and "- Mpox" in sent
    assert "immunizations" in ai.requests[-1]["tools"][0]["input_schema"]["properties"]

    rows = p["immunizations"]
    assert [(v["name"], v["date_given"]) for v in rows] == [
        ("COVID-19", COVID), ("Influenza (flu)", FLU), ("Tdap (tetanus, diphtheria, pertussis)", None), ("Shingrix", FLU)]
    assert rows[0]["vaccine"] == "COVID-19 mRNA (Pfizer)" and rows[0]["lot_number"] == "FA1234"
    assert [v["already_saved"] for v in rows] == [False, True, False, False]
    assert rows[3]["name"] == "Shingrix"  # a match outside the names sent isn't trusted

    body = {"import_id": p["import_id"], "selected_tests": [], "provider_id": 1, "selected_immunizations": [0, 2, 3]}
    # The undated dose needs a date, and nothing is saved until it has one
    r = client.post("/api/pdf/confirm", json=body)
    assert r.status_code == 400 and "Tdap" in r.json()["detail"] and "date it was given" in r.json()["detail"]
    r = client.post("/api/pdf/confirm", json={**body, "immunization_dates": {"2": SOON}})
    assert r.status_code == 400 and "future" in r.json()["detail"]
    assert len(client.get("/api/vaccines/?patient_id=1").json()) == 1

    tdap = (date.today() - timedelta(days=400)).isoformat()
    r = client.post("/api/pdf/batch-confirm", json={"individual_confirmations": [{**body, "immunization_dates": {"2": tdap}}]})
    data = r.json()["data"]
    assert r.status_code == 200 and data["failed_count"] == 0, r.text
    assert [v["name"] for v in data["files"][0]["vaccines"]] == ["COVID-19", "Tdap (tetanus, diphtheria, pertussis)", "Shingrix"]

    shots = {s["vaccine"]: s for s in client.get("/api/vaccines/?patient_id=1").json()}
    assert len(shots) == 4
    covid = shots["COVID-19"]
    assert (covid["date_given"], covid["dose"], covid["manufacturer"], covid["lot_number"], covid["site"], covid["location"]) == (
        COVID, "Booster", "Pfizer", "FA1234", "left arm", "Example Pharmacy")
    assert shots["Tdap (tetanus, diphtheria, pertussis)"]["date_given"] == tdap
    assert shots["Shingrix"]["next_due"] == SOON and shots["Shingrix"]["due_status"] == "due"
    # The dashboard's Vaccines due card picks up the next dose
    assert "Shingrix" in client.get("/").text

    # Saved doses are marked on the review, and the import can't be re-read
    again = client.get(f"/api/pdf/review/{p['import_id']}").json()["immunizations"]
    assert [v["already_imported"] for v in again] == [True, False, True, True]
    assert client.post(f"/api/pdf/rescan-ai/{p['import_id']}").status_code == 400
    # Confirming the same doses again adds nothing
    r = client.post("/api/pdf/confirm", json={**body, "immunization_dates": {"2": tdap}})
    assert r.status_code == 200 and r.json()["data"]["vaccines"] == []
    assert len(client.get("/api/vaccines/?patient_id=1").json()) == 4


def test_already_recorded_dose_is_not_added_twice(client, ai):
    """Ticking a dose that's already on the Vaccines page (e.g. from another document) doesn't duplicate it."""
    setup(client)
    ai.reply = AI_READING
    p = client.post("/api/pdf/upload", files={"file": ("shots.pdf", pdfs.immunization_record(tag="again"), "application/pdf")},
                    params={"ai": "true"}).json()
    r = client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [], "provider_id": 1,
                                               "selected_immunizations": [1]})
    assert r.status_code == 200 and r.json()["data"]["vaccines"] == []
    assert len(client.get("/api/vaccines/?patient_id=1").json()) == 1


def test_vaccines_in_a_visit_summary(client, ai):
    """A visit summary's vaccines are offered with its vitals; they keep their own dates."""
    client.post("/api/providers/", json={"name": "Alex Doe"})
    visit = (date.today() - timedelta(days=5)).isoformat()
    ai.reply = {"visit_date": visit, "tests": [], "medications": [],
                "vitals": [{"kind": "heart_rate", "value": 70, "unit": "bpm"}],
                "immunizations": [{"vaccine": "Flu shot", "matched_vaccine": "Influenza (flu)", "date_given": visit, "site": "left arm"}]}
    p = client.post("/api/pdf/upload", files={"file": ("visit.pdf", pdfs.visit_summary(tag="flu"), "application/pdf")}).json()
    assert p["parser"] == "ai" and p["immunizations"][0]["name"] == "Influenza (flu)"
    r = client.post("/api/pdf/confirm", json={"import_id": p["import_id"], "selected_tests": [], "provider_id": 1,
                                               "selected_vitals": [0], "selected_immunizations": [0]})
    data = r.json()["data"]
    assert r.status_code == 200 and data["vitals_count"] == 1 and len(data["vaccines"]) == 1, r.text
    assert "1 vaccine" in r.json()["message"]
    assert client.get("/api/vaccines/?patient_id=1").json()[0]["date_given"] == visit


def test_vaccine_import_helpers():
    from api.services import vaccine_review
    parsed = {"immunizations": [{"vaccine": "MMR", "date_given": None}, {"vaccine": "HPV", "date_given": "2020-01-01"}]}
    assert vaccine_review.check_immunizations(parsed, [0, 1], {}) == ["MMR: enter the date it was given"]
    assert vaccine_review.check_immunizations(parsed, [0], {"0": "2019-05-05"}) == []
    assert vaccine_review.check_immunizations(parsed, [0], {"0": "nonsense"}) == ["MMR: enter the date it was given"]
    assert vaccine_review.check_immunizations({**parsed, "saved_immunizations": [0]}, [0, 7], {}) == []
