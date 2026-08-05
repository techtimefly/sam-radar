from sam_radar.descriptions import (
    description_url_with_key,
    extract_description_body,
    format_description,
    is_trusted_description_url,
)


def test_description_url_only_trusts_sam_api_hosts():
    url = "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=abc"
    assert is_trusted_description_url(url)
    assert "api_key=test-key" in description_url_with_key(url, "test-key")
    assert not is_trusted_description_url("https://example.test/noticedesc?noticeid=abc")


def test_format_description_strips_html_into_paragraphs():
    formatted = format_description("<div><p>First &amp; main.</p><p>Second line.</p><script>bad()</script></div>")
    assert formatted["available"] is True
    assert formatted["paragraphs"] == ["First & main.", "Second line."]
    assert "bad" not in formatted["text"]


def test_extract_description_body_reads_sam_json_wrapper():
    body = extract_description_body('{"description":"<p>Agency needs DevSecOps support.</p>"}')
    formatted = format_description(body)
    assert formatted["paragraphs"] == ["Agency needs DevSecOps support."]
