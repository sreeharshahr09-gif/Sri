"""Controlled fetcher: SSRF policy, redirects, access restrictions, extraction
quality flags. All offline via injected DNS/transport."""
from conftest import fake_resolve, fake_transport

from tire_scout.fetch import Fetcher


def test_non_http_scheme_blocked(fetcher):
    doc = fetcher.fetch("file:///C:/Users/shreeharsha.r/secret.xlsx")
    assert doc.status == "blocked" and "scheme" in doc.error


def test_credentials_in_url_blocked(fetcher):
    doc = fetcher.fetch("https://user:pass@www.evonik.com/x")
    assert doc.status == "blocked" and "credentials" in doc.error


def test_private_ip_literal_blocked(fetcher):
    doc = fetcher.fetch("http://169.254.169.254/latest/meta-data")
    assert doc.status == "blocked"


def test_hostname_resolving_to_private_blocked(fetcher):
    doc = fetcher.fetch("https://evil-internal.example.com/data")
    assert doc.status == "blocked" and "non-public" in doc.error


def test_dual_homed_host_blocked_if_any_ip_private(fetcher):
    doc = fetcher.fetch("https://dual-homed.example.com/x")
    assert doc.status == "blocked"


def test_dns_failure_reported(fetcher):
    doc = fetcher.fetch("https://unknown-host-zzz.example.com/x")
    assert doc.status == "blocked" and "DNS" in doc.error


def test_redirect_to_private_blocked_after_hop(fetcher):
    doc = fetcher.fetch("https://redirect-evil.example.com/start")
    assert doc.status == "blocked"
    assert "non-public" in doc.error or "blocked host" in doc.error


def test_redirect_chain_to_public_succeeds(fetcher):
    doc = fetcher.fetch("https://redirect-good.example.com/start")
    assert doc.status == "fetched"
    assert doc.final_url == "https://redirect-good.example.com/ok"


def test_payment_required_is_paywalled_not_bypassed(fetcher):
    doc = fetcher.fetch("https://paywalled.example.com/paper")
    assert doc.status == "paywalled"
    assert "not bypassed" in doc.error


def test_forbidden_is_blocked(fetcher):
    doc = fetcher.fetch("https://forbidden.example.com/x")
    assert doc.status == "blocked"


def test_html_extraction_and_meta_date(fetcher):
    doc = fetcher.fetch(
        "https://www.evonik.com/en/news/press-releases/2026/10/silane-silica-ev.html?utm_source=newsletter")
    assert doc.status == "fetched" and doc.extraction_quality == "full"
    assert doc.published_at is not None and doc.published_provenance == "page_meta"
    assert doc.url == "https://evonik.com/en/news/press-releases/2026/10/silane-silica-ev.html"
    assert "12 percent lower rolling resistance" in " ".join(doc.text.split())


def test_pdf_extraction_full(fetcher):
    doc = fetcher.fetch("https://www.hf-mixinggroup.com/downloads/tandem-mixing-2026.pdf")
    assert doc.status == "fetched"
    assert "tandem mixing energy savings" in " ".join(doc.text.split())


def test_scanned_pdf_flagged_not_pretended(fetcher):
    doc = fetcher.fetch("https://scanned.example.com/doc.pdf")
    assert doc.extraction_quality in ("none",)
    assert doc.status in ("fetched", "failed")
    assert doc.text_len == 0


def test_video_is_unsupported(fetcher):
    doc = fetcher.fetch("https://www.youtube.com/watch?v=abc123")
    assert doc.status == "unsupported"
    assert "transcript" in doc.error


def test_abstract_only_flag():
    html = ("<html><head><title>Abstract</title></head><body><div>Abstract only: "
            "we study silica.</div></body></html>")
    pages = {"https://a.example.com/abs": __import__("tire_scout.fetch", fromlist=["FetchResponse"]).FetchResponse(
        200, {"content-type": "text/html"}, html.encode(), "https://a.example.com/abs")}
    f = Fetcher(resolve=lambda h: ["93.184.216.34"], transport=fake_transport(pages))
    doc = f.fetch("https://a.example.com/abs")
    assert doc.extraction_quality == "abstract_only"
