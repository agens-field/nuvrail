"""
Unit tests for the SMTP proxy's send-header extraction and intent-aware
send descriptions (reply/forward detection).
"""
from __future__ import annotations

from gateway.smtp_proxy import (
    _build_send_description,
    _extract_send_headers,
    _extract_subject,
)


def _lines(*lines: str) -> list[bytes]:
    return [(line + "\r\n").encode() for line in lines]


# ---------------------------------------------------------------------------
# _extract_send_headers
# ---------------------------------------------------------------------------


class TestExtractSendHeaders:
    def test_basic_headers(self):
        headers = _extract_send_headers(_lines(
            "From: agent@example.com",
            "To: bob@example.com",
            "Subject: Re: Invoice #1234",
            "In-Reply-To: <abc.123@acme.com>",
            "",
            "Body text Subject: not a header",
        ))
        assert headers["subject"] == "Re: Invoice #1234"
        assert headers["in-reply-to"] == "<abc.123@acme.com>"
        assert "references" not in headers

    def test_folded_references_header(self):
        headers = _extract_send_headers(_lines(
            "Subject: Re: Hello",
            "References: <a@x>",
            " <b@y>",
            "\t<c@z>",
            "",
        ))
        assert headers["references"] == "<a@x> <b@y> <c@z>"

    def test_body_not_scanned(self):
        headers = _extract_send_headers(_lines(
            "Subject: Real",
            "",
            "Subject: Fake (in body)",
            "In-Reply-To: <fake@body>",
        ))
        assert headers["subject"] == "Real"
        assert "in-reply-to" not in headers

    def test_duplicate_header_keeps_first(self):
        headers = _extract_send_headers(_lines(
            "Subject: First",
            "Subject: Second",
            "",
        ))
        assert headers["subject"] == "First"

    def test_extract_subject_fallback(self):
        assert _extract_subject(_lines("From: a@b", "")) == "<no subject>"
        assert _extract_subject(_lines("Subject: Hi", "")) == "Hi"


# ---------------------------------------------------------------------------
# _build_send_description
# ---------------------------------------------------------------------------


class TestBuildSendDescription:
    def test_reply_with_matched_original(self):
        desc = _build_send_description(
            "reply", "Re: Invoice #1234", ["billing@acme.com"],
            {"subject": "Invoice #1234", "sender": "billing@acme.com"},
        )
        assert desc == 'Reply to "Invoice #1234" from billing@acme.com'

    def test_reply_without_match_uses_stripped_subject(self):
        desc = _build_send_description("reply", "Re: Invoice #1234", ["b@x.com"], None)
        assert desc == 'Reply to "Invoice #1234"'

    def test_reply_no_subject_falls_back_to_recipients(self):
        desc = _build_send_description("reply", "", ["bob@example.com"], None)
        assert desc == "Reply to bob@example.com"

    def test_forward(self):
        desc = _build_send_description(
            "forward", "Fwd: Invoice #1234", ["carol@example.com"], None
        )
        assert desc == 'Forward "Invoice #1234" to carol@example.com'

    def test_plain_send_unchanged(self):
        desc = _build_send_description(None, "Hello", ["bob@example.com"], None)
        assert desc == 'Send email to bob@example.com — Subject: "Hello"'
