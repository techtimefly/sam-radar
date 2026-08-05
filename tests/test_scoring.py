from sam_radar.config import BusinessProfile
from sam_radar.scoring import keyword_matches, score_opp, title_key


def profile():
    return BusinessProfile(
        name="Example Technology Services LLC",
        capabilities=["cybersecurity", "software development"],
        naics_secondary=["541512"],
        psc=["DA01", "DJ01"],
        set_asides=["SBA", "SDVOSBC"],
        strong_keywords=["ato", "cybersecurity", "software development"],
        exclude_keywords=["janitorial"],
    )


def test_keyword_matching_uses_word_boundaries():
    assert keyword_matches("ATO support", ["ato"]) == ["ato"]
    assert keyword_matches("stator replacement", ["ato"]) == []


def test_scoring_prefers_configured_codes_and_keywords():
    opp = {
        "title": "Cybersecurity software development support",
        "type": "Sources Sought",
        "naicsCode": "541512",
        "classificationCode": "DA01",
        "typeOfSetAside": "SBA",
    }
    score, reasons = score_opp(profile(), opp)
    assert score >= 10
    assert "PSC DA01" in reasons
    assert "NAICS 541512" in reasons


def test_title_key_dedupes_same_title_and_deadline():
    one = {"title": "  Example  Opportunity ", "responseDeadline": "2026-08-10T12:00:00-04:00"}
    two = {"title": "example opportunity", "responseDeadline": "2026-08-10T12:00:00-04:00"}
    assert title_key(one) == title_key(two)
