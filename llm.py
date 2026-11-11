"""Extraction: facts from a raw report. Language work only — never scoring,
never state.

Two paths, per BRAINSTORM_CHALLENGE_2026-11-11 correction C1:
- If a provider key is configured (env DP_LLM_API_KEY + DP_LLM_BASE_URL),
  an LLM extraction is attempted first, JSON-schema validated, one retry.
- Otherwise (the shipped default) the deterministic rules parser runs and
  the caller records parse_mode: rules in the audit. The parser never
  invents a fact: anything the text does not support becomes unknown and
  a missing_info entry.
"""

import json
import os
import re

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "chhah": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10,
}

MEDICAL_KW = ["saans lene mein takleef", "saans lene mein bahut takleef",
              "breathing difficulty", "breathing problem", "behosh",
              "unconscious", "chest pain", "medical madad", "medical help",
              "heart attack"]
TRAPPED_KW = ["chhat par fasa", "fasa hai", "fasa hua", "fas gaya", "trapped",
              "rooftop", "roof top", "andar fasa", "debris"]
STRANDED_KW = ["stranded", "cannot leave", "nikal nahi sak", "stuck at"]
ELDERLY_KW = ["buzurg", "elderly", "old man", "old woman", "vraddh", "buddha"]
CHILDREN_KW = ["bachch", "children", "kids", "child"]
RISING_ONGOING_KW = ["rain continuing", "barish jaari", "still raining",
                     "aur badh raha hai paani"]
RISING_KW = ["rising", "badh raha", "aa gaya hai", "kamar tak", "waist-deep",
             "waist deep", "badhti ja rahi"]
STABLE_KW = ["stable", "ruk gaya hai", "not rising"]
RECEDING_KW = ["receding", "kam ho raha", "ghat raha"]


def _contains(text, keywords):
    return any(k in text for k in keywords)


def _people_from_text(text):
    if "no people count" in text or "people count given" in text and "no" in text:
        return None
    m = re.search(r"\b(\d+)\s*(log|people|person|aadmi|mahila|vyakti)", text)
    if m:
        return int(m.group(1))
    for word, val in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b\s+(log|people|person|aadmi|family|parivaar)", text):
            return val
    for word, val in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b\s+(mahila|aadmi|parivaar|family)", text):
            return val
    return None


def _location_type(location):
    name = (location or {}).get("name", "").lower()
    if "underpass" in name:
        return "underpass"
    if "basti" in name or "settlement" in (location or {}).get("landmark", "").lower():
        return "settlement"
    if "crossing" in name:
        return "crossing"
    return "location"


def parse_rules(report, location):
    """Deterministic extraction. Returns (facts, missing_info)."""
    text = (report.get("text") or "").lower()
    missing = []

    # Life risk — medical first, then trapped, then stranded.
    if _contains(text, MEDICAL_KW):
        life_risk = "medical_emergency"
        detail = []
        if "behosh" in text or "unconscious" in text:
            detail.append("unconscious")
        if "saans" in text or "breathing" in text:
            detail.append("breathing difficulty")
        if "chest pain" in text:
            detail.append("chest pain")
        life_detail = ", ".join(detail) if detail else None
    elif _contains(text, TRAPPED_KW):
        life_risk = "trapped"
        life_detail = "rooftop" if ("chhat" in text or "rooftop" in text or "roof top" in text) else None
    elif _contains(text, STRANDED_KW):
        life_risk = "stranded"
        life_detail = None
    else:
        life_risk = "none_reported"
        life_detail = None

    people = report.get("people_affected")
    if people is None:
        people = _people_from_text(text)
    if people is None:
        missing.append("people_count")

    if life_risk == "medical_emergency":
        vulnerability, vuln_detail = "elderly_infant_or_medical", "medical vulnerability"
    elif _contains(text, ELDERLY_KW):
        vulnerability, vuln_detail = "elderly_infant_or_medical", "elderly person present"
    elif _contains(text, CHILDREN_KW):
        vulnerability, vuln_detail = "children", None
    else:
        vulnerability, vuln_detail = "none", None

    if _contains(text, RISING_ONGOING_KW):
        progression = "rising_ongoing"
    elif _contains(text, RISING_KW):
        progression = "rising"
    elif _contains(text, RECEDING_KW):
        progression = "receding"
    elif _contains(text, STABLE_KW):
        progression = "stable"
    else:
        progression = "unknown"
        if "hazard_trend" not in missing:
            missing.append("hazard_trend")

    hazard_type = "waterlogging" if "waterlog" in text else "flood"

    facts = {
        "location_code": report.get("location_hint"),
        "hazard_type": hazard_type,
        "life_risk_condition": life_risk,
        "life_risk_detail": life_detail,
        "people_count": people,
        "vulnerability": vulnerability,
        "vulnerability_detail": vuln_detail,
        "hazard_progression": progression,
        "location_type": _location_type(location),
    }
    return facts, missing


FACT_KEYS = {"location_code", "hazard_type", "life_risk_condition",
             "people_count", "vulnerability", "hazard_progression"}
ENUMS = {
    "life_risk_condition": {"medical_emergency", "trapped", "stranded",
                            "exposed_mobile", "none_reported"},
    "vulnerability": {"elderly_infant_or_medical", "children", "none"},
    "hazard_progression": {"rising_ongoing", "rising", "stable", "unknown", "receding"},
    "hazard_type": {"flood", "waterlogging", "heat", "other"},
}


def _llm_extract(report, location):
    """Optional provider path. Returns facts or None on any failure."""
    import httpx

    api_key = os.environ.get("DP_LLM_API_KEY")
    base_url = os.environ.get("DP_LLM_BASE_URL")
    model = os.environ.get("DP_LLM_MODEL", "gpt-4o-mini")
    if not api_key or not base_url:
        return None
    prompt = (
        "Extract facts from this disaster report as JSON with keys: "
        "location_code, hazard_type (flood|waterlogging|heat|other), "
        "life_risk_condition (medical_emergency|trapped|stranded|exposed_mobile|none_reported), "
        "people_count (integer or null — never guess), vulnerability "
        "(elderly_infant_or_medical|children|none), hazard_progression "
        "(rising_ongoing|rising|stable|unknown|receding). "
        f"Location hint: {report.get('location_hint')}. Report: {report.get('text')}"
    )
    for _attempt in range(2):
        try:
            resp = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "response_format": {"type": "json_object"}},
                timeout=20.0)
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
            if not FACT_KEYS.issubset(data.keys()):
                continue
            if any(data[k] not in allowed for k, allowed in ENUMS.items()):
                continue
            data.setdefault("life_risk_detail", None)
            data.setdefault("vulnerability_detail", None)
            data["location_type"] = _location_type(location)
            return data
        except Exception:
            continue
    return None


def extract(report, location):
    """Returns (facts, missing_info, parse_mode)."""
    llm_facts = _llm_extract(report, location)
    if llm_facts is not None:
        missing = []
        if llm_facts.get("people_count") is None:
            missing.append("people_count")
        if llm_facts.get("hazard_progression") == "unknown":
            missing.append("hazard_trend")
        facts = llm_facts
        mode = "llm"
    else:
        facts, missing = parse_rules(report, location)
        mode = "rules"
    # Missing-info discipline (reproduces the published worked examples):
    # hazard trend verification is requested only when the report carries no
    # actionable life-risk signal (the RPT-003 case: no people, no life risk,
    # trend unknown). A report with a life-risk signal is acted on, not sent
    # back for trend verification; its F4 score still uses unknown = 4.
    if (facts.get("hazard_progression") == "unknown"
            and facts.get("life_risk_condition") != "none_reported"):
        missing = [m for m in missing if m != "hazard_trend"]
    return facts, missing, mode
