"""Settings → Clean up: duplicate suggestions (rules and AI) and merging."""

from api.services import cleanup


def make(client, name, panel, unit):
    response = client.post("/api/labs/", json={"name": name, "panel_id": panel, "unit_id": unit}).json()
    return response["data"]["id"] if "data" in response else response["id"]


def setup_data(client):
    for name in ("Chemistry", "Lipid Panel", "Lipid Profile"):
        client.post("/api/panels/", json={"name": name})
    for name in ("g/dL", "mg/dL", "mg/dl", "mmol/L", "K/uL", "x10E3/uL"):
        client.post("/api/units/", json={"name": name})
    labs = {name: make(client, name, panel, unit) for name, panel, unit in [
        ("Albumin", 1, 1), ("Albumin (g/dL)", 1, 1), ("Glucose", 1, 2), ("Glucose (mmol/L)", 1, 4),
        ("Cholesterol, Total", 2, 2), ("Total Cholesterol", 3, 3), ("WBC", 1, 5), ("White Blood Cell Count", 1, 6),
        ("Hemoglobin", 1, 1), ("Hemoglobin A1c", 1, None)]}
    client.post("/api/providers/", json={"name": "Dustin A. Fontenot, PA"})
    for lab, value, provider, day in [("Albumin", 4.5, 1, "2025-01-02"), ("Albumin (g/dL)", 4.4, 2, "2024-09-25"),
                                      ("Albumin (g/dL)", 4.6, 2, "2024-01-01")]:
        client.post("/api/results/", json={"lab_id": labs[lab], "patient_id": 1, "provider_id": provider, "result": value, "date_collected": day})
    return labs


def names(group):
    return sorted(i["name"] for i in group["items"])


def test_rule_suggestions(client):
    setup_data(client)
    client.put("/api/providers/1", json={"name": "Dustin Fontenot"})
    s = client.get("/api/cleanup/suggestions").json()
    groups = [names(g) for g in s["groups"]]
    albumin = next(g for g in s["groups"] if "Albumin" in names(g))
    assert names(albumin) == ["Albumin", "Albumin (g/dL)"] and albumin["name"] == "Albumin"
    assert ["Cholesterol, Total", "Total Cholesterol"] in groups      # word order; mg/dL = mg/dl
    assert not any("Glucose" in g for g in groups)                     # mg/dL vs mmol/L
    assert not any("Hemoglobin" in g for g in groups)                  # Hemoglobin vs A1c
    assert ["Dustin A. Fontenot, PA", "Dustin Fontenot"] in groups      # titles and initials
    assert sum(1 for g in s["groups"] if g["kind"] == "units") == 2


def test_stacked_suffixes_and_overlap():
    lab = lambda i, name, unit="", count=1: {"id": i, "name": name, "unit": unit, "panel": "CMP", "count": count}
    bun = [lab(1, "BUN/Creatinine Ratio", count=4), lab(2, "BUN/Creatinine Ratio (2)"), lab(3, "BUN/Creatinine Ratio (2) (2)")]
    glucose = [lab(20, "Glucose", "mg/dL"), lab(21, "Glucose (2)"), lab(22, "Blood Glucose", "mmol/L")]
    inv = {"labs": bun + glucose, "panels": [], "units": [], "providers": []}
    rules = cleanup.rule_suggestions(inv)
    assert any(sorted(i["id"] for i in g["items"]) == [1, 2, 3] for g in rules)
    ai = [cleanup._group("labs", bun, "same", "ai"), cleanup._group("labs", [glucose[1], glucose[2]], "mixes units", "ai")]
    combined = cleanup.combine(rules, ai)
    ids = [i["id"] for g in combined for i in g["items"]]
    assert len(ids) == len(set(ids))                                    # every item offered once
    assert next(g for g in combined if g["items"][0]["id"] in (1, 2, 3))["source"] == "both"
    assert 22 not in ids                                                # joining would mix units


def test_ai_suggestions(client, ai):
    labs = setup_data(client)
    ai.reply = {"groups": [
        {"kind": "labs", "ids": [labs["WBC"], labs["White Blood Cell Count"]], "keep_id": labs["WBC"], "reason": "Same test"},
        {"kind": "labs", "ids": [labs["Glucose"], labs["Glucose (mmol/L)"]], "keep_id": labs["Glucose"], "reason": "units differ"},
        {"kind": "panels", "ids": [2, 3], "keep_id": 2, "name": "Lipid Panel", "reason": "Same panel"},
        {"kind": "providers", "ids": [1, 999], "keep_id": 1, "reason": "unknown id"},
    ]}
    s = client.post("/api/cleanup/suggestions/ai").json()
    found = [(g["kind"], names(g), g["source"]) for g in s["groups"]]
    assert ("labs", ["WBC", "White Blood Cell Count"], "ai") in found
    assert ("panels", ["Lipid Panel", "Lipid Profile"], "ai") in found
    assert not any("Glucose" in n for _, n, _ in found)
    sent = ai.requests[-1]["messages"][0]["content"]
    assert "Albumin (g/dL)" in sent and "4.6" not in sent              # names only, no results


def test_merge(client):
    labs = setup_data(client)
    r = client.post("/api/cleanup/merge", json={"merges": [
        {"kind": "labs", "keep_id": labs["Albumin"], "merge_ids": [labs["Albumin (g/dL)"]]},
        {"kind": "labs", "keep_id": labs["Glucose"], "merge_ids": [labs["Glucose (mmol/L)"]]}]})
    assert r.status_code == 400 and "different units" in r.json()["detail"]
    assert len(client.get("/api/labs/?limit=100").json()) == 10        # all or nothing

    r = client.post("/api/cleanup/merge", json={"merges": [
        {"kind": "labs", "keep_id": labs["Albumin (g/dL)"], "merge_ids": [labs["Albumin"]], "name": "Albumin"},
        {"kind": "units", "keep_id": 5, "merge_ids": [6]},
        {"kind": "providers", "keep_id": 1, "merge_ids": [2]},
        {"kind": "panels", "keep_id": 2, "merge_ids": [3]}]}).json()
    assert r["success"] and r["merged"][0]["kept"] == "Albumin"
    results = client.get("/api/results/?limit=100").json()["results"]
    albumin = [x for x in results if x["lab"]["name"] == "Albumin"]
    assert len(albumin) == 3 and len({x["lab_id"] for x in albumin}) == 1 and all(x["provider_id"] == 1 for x in albumin)
    labs_now = {lab["name"]: lab for lab in client.get("/api/labs/?limit=100").json()}
    assert labs_now["White Blood Cell Count"]["unit_id"] == 5 and labs_now["Total Cholesterol"]["panel_id"] == 2

    clash = client.post("/api/cleanup/merge", json={"merges": [
        {"kind": "labs", "keep_id": labs["WBC"], "merge_ids": [labs["White Blood Cell Count"]], "name": "Hemoglobin"}]})
    assert clash.status_code == 400 and "already called" in clash.json()["detail"]
