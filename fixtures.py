"""DispatchProof fixture pack — Ghatauli ward (ALL DATA SYNTHETIC).

Transcribed at build time from the on-file prep documents:
  fixtures/ward_gazetteer.json, fixtures/responder_roster.json,
  fixtures/alert_feed_sample.json, PREP_FIXTURES_SPEC.md (incl. the
  2026-11-07 Doom resolutions), PREP_HINDI_TEMPLATES.md.

Ward name per blueprint: Ghatauli. Location names are verbatim from the
gazetteer. Home locations for crews are the PREP_FIXTURES_SPEC addition
(used only by the proximity tie-break in Agent B's matching).
"""

WARD_NAME = "Ghatauli"

# confined: flood-prone AND confined (underpass / low crossing) per rubric F5.
LOCATIONS = [
    {"code": "LOC-01", "name": "Ghatauli Crossing", "landmark": "Old toll plaza",
     "flood_prone": True, "confined": False},
    {"code": "LOC-02", "name": "Mill Compound Lane", "landmark": "Closed textile mill gate",
     "flood_prone": True, "confined": False},
    {"code": "LOC-03", "name": "Station Road Underpass", "landmark": "Railway underpass, east end",
     "flood_prone": True, "confined": True},
    {"code": "LOC-04", "name": "Nagar Palika School", "landmark": "Municipal school, relief shelter point",
     "flood_prone": False, "confined": False},
    {"code": "LOC-05", "name": "Riverside Steps", "landmark": "Main ghat steps",
     "flood_prone": True, "confined": False},
    {"code": "LOC-06", "name": "Chhoti Basti", "landmark": "Low-lying settlement behind the grain market",
     "flood_prone": True, "confined": False},
    {"code": "LOC-07", "name": "Bus Depot Chowk", "landmark": "City bus depot junction",
     "flood_prone": False, "confined": False},
    {"code": "LOC-08", "name": "District Hospital Gate", "landmark": "Hospital main gate, ambulance bay",
     "flood_prone": False, "confined": False},
]

CREWS = [
    {"crew_id": "CREW-1", "name": "Boat Team Alpha",
     "capabilities": ["boat_rescue", "first_aid"], "capacity": 8,
     "home": "LOC-05", "status": "available"},
    {"crew_id": "CREW-2", "name": "Boat Team Bravo",
     "capabilities": ["boat_rescue"], "capacity": 6,
     "home": "LOC-01", "status": "available"},
    {"crew_id": "CREW-3", "name": "Medical Response Unit",
     "capabilities": ["medical"], "capacity": 2,
     "home": "LOC-08", "status": "available"},
    {"crew_id": "CREW-4", "name": "High-Clearance Vehicle Team",
     "capabilities": ["vehicle_evac"], "capacity": 12,
     "home": "LOC-07", "status": "on_assignment"},
    {"crew_id": "CREW-5", "name": "Boat Team Charlie",
     "capabilities": ["boat_rescue", "first_aid"], "capacity": 6,
     "home": "LOC-02", "status": "available"},
    {"crew_id": "CREW-6", "name": "Relief Supply Team",
     "capabilities": ["supply_delivery"], "capacity": 0,
     "home": "LOC-04", "status": "available"},
]

# The alert feed, loaded through the same ingest path a live feed would use.
REPORTS = [
    {"report_id": "RPT-001", "channel": "citizen_message", "lang": "hi",
     "location_hint": "LOC-01", "people_affected": 5,
     "text": "Ghatauli Crossing ke paas paani kamar tak aa gaya hai, ek parivaar "
             "chhat par fasa hai, 5 log hain, ek buzurg bhi hai."},
    {"report_id": "RPT-002", "channel": "forwarded_message", "lang": "en",
     "location_hint": "LOC-01", "people_affected": 5,
     "text": "Family stranded on a rooftop near the old toll plaza, water rising, "
             "five people including an elderly man. Same as the Hindi report, "
             "forwarded by a volunteer."},
    {"report_id": "RPT-003", "channel": "alert_feed", "lang": "en",
     "location_hint": "LOC-03", "people_affected": None,
     "text": "Waterlogging reported at Station Road Underpass. Depth unknown. "
             "No people count given."},
    {"report_id": "RPT-004", "channel": "citizen_message", "lang": "hi",
     "location_hint": "LOC-06", "people_affected": 1,
     "text": "Chhoti Basti mein ek mahila ko saans lene mein takleef ho rahi hai, "
             "turant medical madad chahiye."},
]

# The injected closer (blueprint governs; NOT part of the initial feed).
SOS_REPORT = {
    "report_id": "SOS-001", "channel": "citizen_message", "lang": "hi",
    "location_hint": "LOC-04", "people_affected": 1,
    "text": "Relief point ke paas ek aadmi behosh ho gaya hai, saans lene mein "
            "bahut takleef hai, turant medical madad chahiye.",
}

INCIDENT_SUMMARIES = {
    "LOC-01": "परिवार छत पर फँसा है, पानी कमर तक है।",
    "LOC-06": "एक महिला को साँस लेने में तकलीफ़ है।",
    "LOC-04": "एक आदमी बेहोश है, साँस लेने में बहुत तकलीफ़ है।",
    "LOC-03": "अंडरपास में जलभराव की सूचना है।",
}
