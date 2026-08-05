from pathlib import Path

from sam_radar.config import Settings
from sam_radar.core import (
    delete_search_reference_code,
    save_search_profile,
    save_search_reference_code,
    search_coach,
    search_intelligence,
)
from sam_radar.search_intel import search_reference, set_asides_for_status, suggest_profiles
from sam_radar.storage import Store


def write_profile(path: Path) -> None:
    path.write_text(
        """
business:
  name: Example Technology Services LLC
  dba: ExampleTech
  designations:
    - Service-Disabled Veteran-Owned Small Business
capabilities:
  - DevSecOps and security automation
  - Technical training
naics:
  primary:
    - "541512"
psc:
  - DJ01
keywords:
  strong:
    - CMMC
""".lstrip()
    )


def test_reference_lookup_finds_naics_and_psc_by_description():
    naics = search_reference("naics", "computer systems")
    psc = search_reference("psc", "security compliance")

    assert naics[0]["code"] == "541512"
    assert psc[0]["code"] == "DJ01"


def test_set_asides_hint_eligibility_from_status():
    set_asides = set_asides_for_status(["Service-Disabled Veteran-Owned Small Business"])
    by_code = {item["code"]: item for item in set_asides}

    assert by_code["SDVOSBC"]["eligibleHint"] is True
    assert by_code["SBA"]["eligibleHint"] is True


def test_suggest_profiles_from_capability_text():
    suggestion = suggest_profiles("DevSecOps CMMC secure CI/CD software training", ["SDVOSB"])
    profile = suggestion["profile"]

    assert "541512" in profile["naics"]
    assert "DJ01" in profile["psc"]
    assert "SDVOSBC" in profile["setAsides"]
    assert any("CMMC" in keyword for keyword in profile["keywords"])


def test_saved_codes_profiles_and_core_search_intelligence(tmp_path: Path):
    profile_path = tmp_path / "business.yaml"
    write_profile(profile_path)
    settings = Settings(sam_gov_api_key="test-key", profile_path=profile_path, data_dir=tmp_path / "data")

    saved_code = save_search_reference_code(
        settings,
        {"kind": "naics", "code": "541512", "title": "Computer Systems Design Services", "description": "Systems design"},
    )
    saved_profile = save_search_profile(
        settings,
        {"name": "DevSecOps", "keywords": ["CMMC"], "naics": ["541512"], "psc": ["DJ01"], "setAsides": ["SDVOSBC"], "active": True},
    )
    intel = search_intelligence(settings, "security")

    assert saved_code["code"]["code"] == "541512"
    assert saved_profile["profile"]["name"] == "DevSecOps"
    assert intel["savedCodes"][0]["code"] == "541512"
    deleted = delete_search_reference_code(settings, {"kind": "naics", "code": "541512"})
    after_delete = search_intelligence(settings, "security")

    assert deleted["code"]["deleted"] is True
    assert after_delete["savedCodes"] == []
    assert intel["profiles"][0]["name"] == "DevSecOps"
    assert intel["profileQuality"][0]["recommendation"] in {"Monitor", "Tune"}


def test_search_coach_is_deterministic_without_llm(tmp_path: Path):
    profile_path = tmp_path / "business.yaml"
    write_profile(profile_path)
    settings = Settings(sam_gov_api_key="test-key", profile_path=profile_path, data_dir=tmp_path / "data")

    result = search_coach(settings, {"text": "DevSecOps CMMC software modernization"})

    assert result["ok"] is True
    assert result["mode"] == "deterministic"
    assert "541512" in result["profile"]["naics"]


def test_storage_feedback_summary(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    store.add_search_feedback({"noticeId": "abc", "reason": "not-it-security", "notes": "Bad match"})

    summary = store.search_feedback_summary()

    assert summary == [{"reason": "not-it-security", "count": 1, "lastSeen": summary[0]["lastSeen"]}]
