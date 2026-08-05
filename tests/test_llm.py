from sam_radar.config import Settings
from sam_radar.llm import LLMClient, ai_mode, normalized_provider, settings_summary


def test_ai_defaults_to_no_ai_mode():
    settings = Settings(sam_gov_api_key="test")

    summary = settings_summary(settings)
    result = LLMClient(settings).test_connection()

    assert normalized_provider(settings) == "none"
    assert ai_mode(settings) == "disabled"
    assert summary["apiKeyConfigured"] is False
    assert summary["external"] is False
    assert summary["auditEnabled"] is True
    assert summary["privacyLabel"] == "No AI provider active"
    assert result["ok"] is True
    assert "deterministic/no-AI" in result["message"]


def test_cloud_mode_reports_privacy_warning_without_exposing_token():
    settings = Settings(
        sam_gov_api_key="test",
        enable_ai_assist=True,
        llm_provider="openai-compatible",
        llm_base_url="https://api.example.test/v1",
        llm_model="model-a",
        llm_api_key="secret-token",
    )

    summary = settings_summary(settings)

    assert summary["provider"] == "openai-compatible"
    assert summary["mode"] == "cloud"
    assert summary["apiKeyConfigured"] is True
    assert summary["external"] is True
    assert "External AI" in summary["privacyLabel"]
    assert "secret-token" not in str(summary)
    assert any("external service" in warning for warning in summary["warnings"])


def test_disabled_provider_settings_warn_when_ai_assist_off():
    settings = Settings(sam_gov_api_key="test", llm_provider="ollama", llm_model="llama3")

    summary = settings_summary(settings)

    assert summary["mode"] == "disabled"
    assert summary["enabled"] is False
    assert "AI assist is disabled" in summary["warnings"][0]
