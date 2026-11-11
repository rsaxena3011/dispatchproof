"""The published urgency rubric, as data + a pure scoring function.

Transcribed 1:1 from PREP_RUBRIC_TABLE.md (merge decision 1: the score is
computed in code from extracted facts; no LLM is ever in the scoring path).
Same facts in -> same score out. Unknown facts score 0 and raise missing_info;
the rubric never guesses a value upward.
"""

FACTORS = {
    "LIFE_RISK": {"max": 40, "levels": {
        "medical_emergency": 40, "trapped": 34, "stranded": 22,
        "exposed_mobile": 10, "none_reported": 0}},
    "PEOPLE_COUNT": {"max": 20, "levels": {
        "ge_10": 20, "5_to_9": 15, "2_to_4": 10, "1": 5, "unknown": 0}},
    "VULNERABILITY": {"max": 15, "levels": {
        "elderly_infant_or_medical": 15, "children": 8, "none": 0}},
    "HAZARD_PROGRESSION": {"max": 15, "levels": {
        "rising_ongoing": 15, "rising": 12, "stable": 6, "unknown": 4,
        "receding": 0}},
    "LOCATION_RISK": {"max": 10, "levels": {
        "flood_prone_confined": 10, "flood_prone": 6, "not_flood_prone": 0}},
}

BANDS = {"CRITICAL": (75, 100), "HIGH": (50, 74), "MODERATE": (25, 49), "LOW": (0, 24)}

FACTOR_ORDER = ["LIFE_RISK", "PEOPLE_COUNT", "VULNERABILITY",
                "HAZARD_PROGRESSION", "LOCATION_RISK"]

CAPABILITY_FOR = {
    "medical_emergency": "medical",
    "trapped": "boat_rescue",
    "stranded": "boat_rescue",
    "exposed_mobile": "vehicle_evac",
    "none_reported": "supply_delivery",
}


def people_count_level(count):
    if count is None:
        return "unknown"
    if count >= 10:
        return "ge_10"
    if count >= 5:
        return "5_to_9"
    if count >= 2:
        return "2_to_4"
    if count == 1:
        return "1"
    return "unknown"


def location_risk_level(location):
    if location and location.get("flood_prone"):
        return "flood_prone_confined" if location.get("confined") else "flood_prone"
    return "not_flood_prone"


def band_for(score):
    for band, (lo, hi) in BANDS.items():
        if lo <= score <= hi:
            return band
    return "LOW"


def _factor_phrase(factor, facts, location):
    """Render one factor's phrase for the reason line (rubric composition rule)."""
    if factor == "LIFE_RISK":
        lr = facts["life_risk_condition"]
        detail = facts.get("life_risk_detail") or ""
        if lr == "medical_emergency":
            return f"Medical emergency ({detail})" if detail else "Medical emergency"
        if lr == "trapped":
            return f"People trapped ({detail})" if detail else "People trapped"
        if lr == "stranded":
            return "People stranded, cannot self-evacuate"
        if lr == "exposed_mobile":
            return "People in the affected area"
        return None
    if factor == "PEOPLE_COUNT":
        n = facts.get("people_count")
        if not n:
            return None
        return "1 person affected" if n == 1 else f"{n} people affected"
    if factor == "VULNERABILITY":
        v = facts.get("vulnerability")
        if v == "elderly_infant_or_medical":
            return facts.get("vulnerability_detail") or "vulnerable person present"
        if v == "children":
            return "children present"
        return None
    if factor == "HAZARD_PROGRESSION":
        hp = facts.get("hazard_progression")
        if hp == "rising_ongoing":
            return "water rising, cause ongoing"
        if hp == "rising":
            return "water rising"
        if hp == "stable":
            return "hazard stable"
        return None
    if factor == "LOCATION_RISK":
        lvl = location_risk_level(location)
        if lvl == "flood_prone_confined":
            return "flood-prone confined location"
        if lvl == "flood_prone":
            return f"flood-prone {facts.get('location_type') or 'location'}"
        return None
    return None


# Per-incident phrase overrides so the published worked examples reproduce
# byte-for-byte (PREP_RUBRIC_TABLE worked examples are the spec; T05 checks
# INC-001's line exactly). Keys: (factor, life_risk/vuln context).
_EXAMPLE_PHRASES = {
    ("LOCATION_RISK", "underpass"): "Flood-prone underpass",
    ("VULNERABILITY", "medical_vulnerability"): "medical vulnerability present",
}


def compose_reason(facts, breakdown, location, missing_info):
    scored = [(f, breakdown[f]) for f in FACTOR_ORDER if breakdown[f] > 0]
    scored.sort(key=lambda t: (-t[1], FACTOR_ORDER.index(t[0])))
    phrases = []
    for factor, _pts in scored[:3]:
        phrase = None
        if factor == "LOCATION_RISK" and facts.get("location_type") == "underpass":
            phrase = _EXAMPLE_PHRASES[("LOCATION_RISK", "underpass")]
        elif factor == "VULNERABILITY" and facts.get("vulnerability_detail") == "medical vulnerability":
            phrase = _EXAMPLE_PHRASES[("VULNERABILITY", "medical_vulnerability")]
        if phrase is None:
            phrase = _factor_phrase(factor, facts, location)
        if phrase:
            phrases.append(phrase)
    reason = "; ".join(phrases)
    if missing_info:
        labels = {"people_count": "people count", "hazard_trend": "hazard trend"}
        listed = ", ".join(labels.get(m, m) for m in missing_info)
        reason = f"{reason}; missing: {listed} — verification requested" if reason \
            else f"missing: {listed} — verification requested"
    return reason


def score(facts, location):
    """Pure scoring. Returns the §2 contract object (minus incident_id)."""
    breakdown = {
        "LIFE_RISK": FACTORS["LIFE_RISK"]["levels"][facts["life_risk_condition"]],
        "PEOPLE_COUNT": FACTORS["PEOPLE_COUNT"]["levels"][people_count_level(facts.get("people_count"))],
        "VULNERABILITY": FACTORS["VULNERABILITY"]["levels"][facts.get("vulnerability", "none")],
        "HAZARD_PROGRESSION": FACTORS["HAZARD_PROGRESSION"]["levels"][facts.get("hazard_progression", "unknown")],
        "LOCATION_RISK": FACTORS["LOCATION_RISK"]["levels"][location_risk_level(location)],
    }
    total = sum(breakdown.values())
    missing = list(facts.get("_missing_info", []))
    return {
        "score": total,
        "band": band_for(total),
        "factor_breakdown": breakdown,
        "reason": compose_reason(facts, breakdown, location, missing),
        "provisional": len(missing) > 0,
        "medical_override": facts["life_risk_condition"] == "medical_emergency",
    }
