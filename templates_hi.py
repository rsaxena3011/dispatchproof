"""Fixed Hindi templates (PREP_HINDI_TEMPLATES.md) — no free-form generation.

Templates state status only; none contains a promise. Slot values never
invent facts: an unknown people count renders the 'not available' wording.
"""

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

TASK_TYPES = {
    "boat_rescue": "नाव द्वारा बचाव",
    "medical": "चिकित्सा सहायता",
    "vehicle_evac": "वाहन द्वारा निकासी",
    "supply_delivery": "राहत सामग्री पहुँचाना",
}

ACK_REQUEST = "कृपया प्राप्ति की पुष्टि करें:"
RULES_MODE_LINE = "स्वचालित अनुवाद उपलब्ध नहीं है — नियम-आधारित जानकारी दिखाई जा रही है।"


def _time_ist(iso_ts=None):
    if iso_ts:
        try:
            dt = datetime.fromisoformat(iso_ts)
        except ValueError:
            dt = datetime.now(IST)
    else:
        dt = datetime.now(IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%H:%M")


def task_message(capability, location, summary, people_count, reason):
    """H2 — the responder inbox task message."""
    people_line = str(people_count) if people_count else "संख्या उपलब्ध नहीं"
    return "\n".join([
        f"कार्य: {TASK_TYPES.get(capability, capability)} — {location['name']} ({location['code']})",
        f"विवरण: {summary}",
        f"लोग: {people_line}",
        f"स्थिति: {reason}",
    ])


def area_update(location, status_key, crew_name=None, ts=None):
    """H1 — one line per location with an active incident."""
    status_lines = {
        "new": "नई सूचना मिली है, जाँच चल रही है।",
        "dispatched": "मदद भेज दी गई है।",
        "acknowledged": "टीम ने सूचना की पुष्टि कर दी है।",
        "none": "फिलहाल कोई सक्रिय सूचना नहीं है।",
    }
    crew_line = f"टीम: {crew_name}" if crew_name else "टीम तय नहीं हुई है।"
    return (f"{location['name']} ({location['code']}): "
            f"{status_lines[status_key]} | {crew_line} | समय {_time_ist(ts)}")
