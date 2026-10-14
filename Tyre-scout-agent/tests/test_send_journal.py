"""Send journal: stable identities, no blind resend, uncertain state, allowlist."""
import pytest

from tire_scout.emailer.journal import (OutboundMessage, mark, prepare_send,
                                        send_identity, uncertain_sends,
                                        validate_message, validate_recipients)


def _msg(cfg, to=None, subject="Daily", attachments=None):
    return OutboundMessage(subject=subject, from_address=cfg.email.from_address,
                           to=to or list(cfg.recipients.daily_to), cc=[],
                           html="<p>hi</p>", text="hi", attachments=attachments or [])


def test_identity_ignores_subject_recipients_render():
    a = send_identity("daily", "2026-10-12", "shreeharsha.r@apollotyres.com")
    b = send_identity("daily", "2026-10-12", "Shreeharsha.R@ApolloTyres.com")
    assert a == b
    assert a != send_identity("daily", "2026-10-13", "shreeharsha.r@apollotyres.com")
    assert a != send_identity("weekly", "2026-10-12", "shreeharsha.r@apollotyres.com")


def test_recipient_allowlist_enforced(cfg):
    with pytest.raises(ValueError):
        validate_recipients(cfg, ["attacker@gmail.com"], [])
    with pytest.raises(ValueError):
        validate_recipients(cfg, ["colleague@gmail.com"], [])
    validate_recipients(cfg, ["shreeharsha.r@apollotyres.com"], [])  # ok


def test_from_address_must_match_approved_account(cfg):
    msg = _msg(cfg)
    msg.from_address = "other@apollotyres.com"
    with pytest.raises(ValueError):
        validate_message(cfg, msg)


def test_attachment_validation(cfg, tmp_path):
    bad = tmp_path / "evil.exe"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError):
        validate_message(cfg, _msg(cfg, attachments=[bad]))
    with pytest.raises(ValueError):
        validate_message(cfg, _msg(cfg, attachments=[tmp_path / "missing.pdf"]))


def test_no_resend_after_submission(cfg, db):
    msg = _msg(cfg)
    d1 = prepare_send(db, digest_type="daily", period="2026-10-12", cfg=cfg, msg=msg)
    assert d1.allowed
    mark(db, d1.identity, "submitted", provider="smtp", provider_id="smtp://x/1")
    d2 = prepare_send(db, digest_type="daily", period="2026-10-12", cfg=cfg,
                      msg=_msg(cfg, subject="RETRY changed subject"))
    assert not d2.allowed and "submitted" in d2.reason


def test_uncertain_blocks_resend_until_reconciled(cfg, db):
    msg = _msg(cfg)
    d1 = prepare_send(db, digest_type="weekly", period="2026-10-05", cfg=cfg, msg=msg)
    mark(db, d1.identity, "uncertain", error="timeout during DATA")
    d2 = prepare_send(db, digest_type="weekly", period="2026-10-05", cfg=cfg, msg=msg)
    assert not d2.allowed
    assert uncertain_sends(db)[0]["identity"] == d1.identity
    # operator reconciles to a terminal state, then a retry is possible
    mark(db, d1.identity, "failed_before_submit")
    d3 = prepare_send(db, digest_type="weekly", period="2026-10-05", cfg=cfg, msg=msg)
    assert d3.allowed


def test_retry_allowed_after_clean_failure(cfg, db):
    msg = _msg(cfg)
    d1 = prepare_send(db, digest_type="daily", period="2026-10-12", cfg=cfg, msg=msg)
    mark(db, d1.identity, "failed_before_submit", error="connection refused")
    d2 = prepare_send(db, digest_type="daily", period="2026-10-12", cfg=cfg, msg=msg)
    assert d2.allowed
