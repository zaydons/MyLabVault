"""AI reading (Bedrock mocked): parsing, re-scan, the reader comparison and switching back."""

import pdfs

REPORT = pdfs.lab_report([["Cholesterol, Total", "185", "", "mg/dL", "100-199"], ["Triglycerides", "210", "High", "mg/dL", "0-149"],
                          ["Glucose", "88", "", "mg/dL", "70-99"]],
                         header_lines=["Date Collected: 01/15/2026   Ordering Physician: Dr Jane Smith"])
AI_READING = {"collection_date": "2026-01-16", "ordering_provider": "Dr Jane Smith", "tests": [
    {"name": "Total Cholesterol", "value": 185, "unit": "mg/dL", "ref_low": 100, "ref_high": 199, "ref_text": "100-199"},
    {"name": "Triglycerides", "value": 201, "unit": "mg/dL", "ref_low": 0, "ref_high": 149, "ref_text": "0-149", "flag": "H"},
    {"name": "HDL", "value": 52, "unit": "mg/dL", "ref_low": 39, "ref_text": ">39"},
]}


def test_ai_disabled(client):
    assert client.get("/api/pdf/ai-status").json()["enabled"] is False
    r = client.post("/api/pdf/upload", files={"file": ("r.pdf", REPORT, "application/pdf")}, params={"ai": "true"})
    assert r.status_code == 400 and "not enabled" in r.json()["detail"]


def test_ai_reading_and_errors(client, ai):
    ai.reply = AI_READING
    r = client.post("/api/pdf/upload", files={"file": ("r.pdf", REPORT, "application/pdf")}, params={"ai": "true"}).json()
    assert r["parser"] == "ai" and r["date_collected"] == "2026-01-16" and r["total_tests_found"] == 3
    assert ">39" in r["tests"][2]["reference_range"]["text"]
    sent = ai.requests[-1]
    assert sent["messages"][0]["content"][0]["type"] == "document"

    ai.status = 403
    r = client.post("/api/pdf/upload", files={"file": ("other.pdf", pdfs.lab_report([["A", "1", "", "", ""]], tag="x"), "application/pdf")},
                    params={"ai": "true"})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]


def test_ai_per_result_dates():
    from api.services import ai_parser
    with_date = ai_parser._to_parser_test(ai_parser.ExtractedTest(name="WBC", value=7.8, collection_date="2024-09-25"), {})
    without = ai_parser._to_parser_test(ai_parser.ExtractedTest(name="WBC", value=7.8), {})
    assert with_date["date_collected"] == "2024-09-25" and without["date_collected"] is None


def test_rescan_comparison_and_switch(client, ai):
    first = client.post("/api/pdf/upload", files={"file": ("r.pdf", REPORT, "application/pdf")}).json()
    iid = first["import_id"]
    assert first["parser"] != "ai" and first["comparison"] is None
    built_in = [t["name"] for t in first["tests"]]

    ai.reply = AI_READING
    r = client.post(f"/api/pdf/rescan-ai/{iid}").json()
    cmp = r["comparison"]
    assert r["parser"] == "ai" and cmp["active"] == "ai"
    rows = {row["name"]: row for row in cmp["rows"]}
    assert rows["Total Cholesterol"]["change"] == "same"
    assert rows["Triglycerides"]["change"] == "different" and rows["Triglycerides"]["differences"] == ["result"]
    assert rows["HDL"]["change"] == "only_ai" and rows["Glucose"]["change"] == "only_standard"
    assert cmp["summary"] == {"same": 1, "different": 1, "only_ai": 1, "only_standard": 1}
    assert [f["same"] for f in cmp["fields"]] == [False, True]  # date differs; "Dr Jane Smith" = "Jane Smith"

    again = client.post(f"/api/pdf/rescan-ai/{iid}").json()  # still compared with the built-in reading
    assert again["comparison"]["standard_count"] == len(built_in)

    back = client.post(f"/api/pdf/{iid}/switch-reading").json()
    assert back["parser"] == "standard" and [t["name"] for t in back["tests"]] == built_in
    ai_again = client.post(f"/api/pdf/{iid}/switch-reading").json()
    hdl = next(t for t in ai_again["tests"] if t["name"] == "HDL")
    saved = client.post("/api/pdf/confirm", json={"import_id": iid, "selected_tests": [hdl["index"]], "provider_id": 1}).json()
    assert saved["data"]["results"][0]["name"] == "HDL"
    blocked = client.post(f"/api/pdf/{iid}/switch-reading")
    assert blocked.status_code == 400 and "already been saved" in blocked.json()["detail"]
