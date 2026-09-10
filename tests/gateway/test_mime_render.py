"""
Tests for gateway.mime_render (issue #154).

The renderer must turn a raw RFC 2822 DATA payload into readable text for the
"Read full message" approval view, reversing Content-Transfer-Encoding
(base64 / quoted-printable) and reducing HTML to rough text — WITHOUT ever being
the thing we relay. The proxy stores this alongside the untouched raw body.

Test matrix:
  - bare body (no headers)        → returned unchanged
  - base64 text/plain             → decoded to readable text
  - quoted-printable text/plain   → decoded to readable text
  - 7bit / plain text/plain       → passthrough
  - multipart/alternative         → text/plain part wins over text/html
  - html-only                     → reduced to rough text
  - non-UTF-8 charset (base64)    → decoded via declared charset
  - attachment-only               → falls open to raw (nothing readable inline)
  - malformed / undecodable       → falls open to raw
  - relay-safety property         → render_body never claims to be the raw body
                                     for relay (caller stores it separately); we
                                     assert the raw input is not mutated.
"""
from __future__ import annotations

import base64
import quopri

from gateway.mime_render import render_body


def _rfc822(headers: dict[str, str], body: str) -> str:
    head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return f"{head}\r\n{body}"


def test_bare_body_no_headers_returned_unchanged():
    raw = "Just a plain line.\nAnd another."
    # No headers → already readable; normalised newlines only.
    assert render_body(raw) == "Just a plain line.\nAnd another."


def test_empty_body_returned_unchanged():
    assert render_body("") == ""


def test_base64_text_plain_is_decoded():
    plain = "Hello reviewer,\n\nThis was base64 encoded.\nApprove me if it reads right.\n"
    encoded = base64.b64encode(plain.encode("utf-8")).decode("ascii")
    raw = _rfc822(
        {
            "From": "agent@example.com",
            "To": "alice@example.com",
            "Subject": "Encoded",
            "MIME-Version": "1.0",
            "Content-Type": 'text/plain; charset="utf-8"',
            "Content-Transfer-Encoding": "base64",
        },
        encoded,
    )
    out = render_body(raw)
    assert "This was base64 encoded." in out
    assert "Approve me if it reads right." in out
    # The base64 blob itself must NOT appear in the rendered output.
    assert encoded.strip() not in out


def test_quoted_printable_text_plain_is_decoded():
    plain = "Cost is 50=/mo? No — it costs 50 EUR.\nCafé, naïve, façade.\n"
    encoded = quopri.encodestring(plain.encode("utf-8")).decode("ascii")
    raw = _rfc822(
        {
            "Content-Type": 'text/plain; charset="utf-8"',
            "Content-Transfer-Encoding": "quoted-printable",
        },
        encoded,
    )
    out = render_body(raw)
    assert "it costs 50 EUR." in out
    assert "Café, naïve, façade." in out


def test_plain_7bit_passthrough():
    raw = _rfc822(
        {
            "Content-Type": "text/plain; charset=us-ascii",
            "Content-Transfer-Encoding": "7bit",
        },
        "Nothing encoded here.\n",
    )
    out = render_body(raw)
    assert "Nothing encoded here." in out


def test_multipart_alternative_prefers_plain_over_html():
    plain_part = "PLAIN: read me first.\n"
    html_part = "<html><body><p>HTML: ignore me when plain exists.</p></body></html>"
    boundary = "BOUND123"
    body = (
        f"--{boundary}\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
        f"{plain_part}\r\n"
        f"--{boundary}\r\n"
        'Content-Type: text/html; charset="utf-8"\r\n\r\n'
        f"{html_part}\r\n"
        f"--{boundary}--\r\n"
    )
    raw = _rfc822(
        {
            "MIME-Version": "1.0",
            "Content-Type": f'multipart/alternative; boundary="{boundary}"',
        },
        body,
    )
    out = render_body(raw)
    assert "PLAIN: read me first." in out
    assert "HTML: ignore me" not in out


def test_html_only_is_reduced_to_text():
    html_body = (
        "<html><head><style>p{color:red}</style></head>"
        "<body><p>First paragraph.</p><p>Second paragraph.</p>"
        "<script>alert('x')</script></body></html>"
    )
    raw = _rfc822(
        {
            "MIME-Version": "1.0",
            "Content-Type": 'text/html; charset="utf-8"',
        },
        html_body,
    )
    out = render_body(raw)
    assert "First paragraph." in out
    assert "Second paragraph." in out
    # Tags, script, and style content stripped.
    assert "<p>" not in out
    assert "alert('x')" not in out
    assert "color:red" not in out


def test_base64_non_utf8_charset_is_decoded():
    # Latin-1 content with a byte (0xE9 = 'é') that is invalid UTF-8.
    plain = "Café München"
    encoded = base64.b64encode(plain.encode("latin-1")).decode("ascii")
    raw = _rfc822(
        {
            "Content-Type": 'text/plain; charset="iso-8859-1"',
            "Content-Transfer-Encoding": "base64",
        },
        encoded,
    )
    out = render_body(raw)
    assert "Café München" in out


def test_attachment_only_falls_open_to_raw():
    # A single application/octet-stream attachment part, no text body.
    boundary = "ATT1"
    payload = base64.b64encode(b"\x00\x01\x02binary").decode("ascii")
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/octet-stream\r\n"
        'Content-Disposition: attachment; filename="blob.bin"\r\n'
        "Content-Transfer-Encoding: base64\r\n\r\n"
        f"{payload}\r\n"
        f"--{boundary}--\r\n"
    )
    raw = _rfc822(
        {
            "MIME-Version": "1.0",
            "Content-Type": f'multipart/mixed; boundary="{boundary}"',
        },
        body,
    )
    out = render_body(raw)
    # No readable text part → fall open to (normalised) raw, not blank.
    assert out
    assert "octet-stream" in out


def test_malformed_input_falls_open_to_raw():
    # Header-looking but structurally broken multipart with a missing boundary.
    raw = (
        "Content-Type: multipart/alternative; boundary=\r\n\r\n"
        "totally not valid mime <<< >>>"
    )
    out = render_body(raw)
    # Must not raise and must return *something* readable (the raw content).
    assert "totally not valid mime" in out


def test_render_does_not_mutate_input_and_is_pure():
    plain = "keep me exact"
    encoded = base64.b64encode(plain.encode()).decode("ascii")
    raw = _rfc822(
        {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Transfer-Encoding": "base64",
        },
        encoded,
    )
    raw_snapshot = str(raw)
    _ = render_body(raw)
    # render_body must be pure — the caller relies on the raw string it passed in
    # remaining byte-identical (that same string is what gets relayed).
    assert raw == raw_snapshot


def test_crlf_normalised_to_lf():
    raw = _rfc822(
        {"Content-Type": "text/plain; charset=utf-8"},
        "line one\r\nline two\r\n",
    )
    out = render_body(raw)
    assert "\r" not in out
    assert "line one\nline two" in out
