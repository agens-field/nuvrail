"""
MIME body renderer — Issue #154.

Turns the *raw* RFC 2822 DATA payload an agent handed the SMTP proxy into a
**human-readable** rendering for the approval card's "Read full message" view.

Why this exists
---------------
The proxy stores the outbound body verbatim (``smtp_envelope["body"]``) because
that exact byte sequence is what we relay on approval — Nuvrail's guarantee is
"review *exactly* what will be sent," and re-encoding could change the delivered
message. But "verbatim" means that when the agent used
``Content-Transfer-Encoding: base64`` (or quoted-printable), a human reviewer
sees an unreadable blob and cannot meaningfully approve.

This module produces a **derived, display-only** rendering. It never touches the
raw ``body`` used for relay; callers store the result in a *separate* field
(``body_rendered``) alongside the untouched raw body.

Decode pipeline
---------------

    raw DATA payload (str)
        │
        ▼
    email.message_from_string()  ── no headers? ──►  return raw unchanged
        │ (parsed EmailMessage)                       (already plain text)
        ▼
    walk parts, pick best text part:
        text/plain  ─── preferred ───┐
        text/html   ─── fallback  ───┤
        (skip attachments: has a     │
         filename / Content-Disp.)   │
        ▼                            ▼
    get_content() (stdlib decodes CTE + charset)
        │  html?  ──►  crude tag/entity strip → text
        ▼
    normalise newlines → rendered text (str)

    Any decode failure at any stage ──►  return the raw payload unchanged
    (fail *open* to the raw bytes — a readable-ish blob beats a blank card, and
     the reviewer can still fall back to inspecting it).

Scope: text rendering for review only. This is NOT a security boundary and NOT
a full MIME renderer — no image inlining, no CSS, no link following. HTML is
reduced to rough plain text; the raw body remains available if a reviewer needs
the exact source.
"""
from __future__ import annotations

import email
import html
import logging
import re
from email.message import EmailMessage, Message

logger = logging.getLogger(__name__)

# Cap the rendered output so a pathological body can't blow up the API payload
# or the browser. The raw body has its own (separate) size handling upstream.
_MAX_RENDERED_CHARS = 100_000

# Very small HTML→text reduction. Deliberately crude: we are giving a reviewer a
# readable gist, not rendering a page. The raw body stays available for exactness.
_BLOCK_BREAK_RE = re.compile(
    r"</\s*(p|div|br|li|tr|h[1-6]|table|ul|ol|blockquote)\s*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _html_to_text(raw_html: str) -> str:
    """Reduce an HTML part to rough plain text for review.

    Drops <script>/<style> content, turns common block-closers into newlines,
    strips remaining tags, and unescapes entities. Not a sanitizer and not a
    faithful renderer — a readable approximation only.
    """
    text = _SCRIPT_STYLE_RE.sub("", raw_html)
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # Collapse runs of blank lines and trailing whitespace produced by stripping.
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln.strip():
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _part_text(part: Message) -> str | None:
    """Decode a single leaf part's payload to text, honoring CTE + charset.

    Returns None if the part can't be decoded to text.
    """
    # get_content() (EmailMessage API) transparently reverses the
    # Content-Transfer-Encoding (base64 / quoted-printable / 7bit / 8bit) and
    # decodes per the declared charset. Fall back to the legacy get_payload path
    # for a plain email.message.Message.
    try:
        if isinstance(part, EmailMessage):
            content = part.get_content()
            return content if isinstance(content, str) else None
    except Exception as exc:
        logger.debug("[mime_render] EmailMessage.get_content failed: %s", exc)

    try:
        payload = part.get_payload(decode=True)
    except Exception as exc:
        logger.debug("[mime_render] get_payload(decode=True) failed: %s", exc)
        return None
    if payload is None:
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        # Unknown/invalid charset label — best-effort UTF-8.
        return payload.decode("utf-8", errors="replace")


def _is_attachment(part: Message) -> bool:
    """True if the part looks like an attachment rather than inline body text."""
    disp = (part.get_content_disposition() or "").lower()
    if disp == "attachment":
        return True
    # A named part with no explicit inline disposition is treated as an
    # attachment for review purposes (we render the message text, not files).
    return bool(part.get_filename())


def _best_text_from_message(msg: Message) -> str | None:
    """Walk a (possibly multipart) message and return the best body text.

    Preference order: the first text/plain leaf, else the first text/html leaf
    (reduced to text). Attachment parts are skipped.
    """
    plain: str | None = None
    html_text: str | None = None

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        if _is_attachment(part):
            continue
        text = _part_text(part)
        if not text:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html_text is None:
            html_text = _html_to_text(text)
        if plain is not None:
            # text/plain wins outright — stop as soon as we have one.
            break

    return plain if plain is not None else html_text


def render_body(raw_body: str) -> str:
    """Render a raw RFC 2822 DATA payload to human-readable text for review.

    Display-only. Never mutates or is used in place of the raw body for relay.

    Behavior:
      - Full RFC 2822 message (has headers): parse, decode the best text part
        (CTE + charset aware), reduce HTML-only bodies to rough text.
      - Bare payload with no headers: returned unchanged (already plain text).
      - Any parse/decode failure: returned unchanged (fail open to raw).
      - Result is normalised to ``\\n`` newlines and length-capped.

    Args:
        raw_body: the exact DATA payload captured by the SMTP proxy.

    Returns:
        A readable text rendering (may equal ``raw_body`` when it is already
        plain text or when decoding was not possible).
    """
    if not raw_body:
        return raw_body

    try:
        parsed = email.message_from_string(raw_body)
    except Exception as exc:
        # Any parse failure on malformed input → fall open to the raw payload.
        logger.debug("[mime_render] message_from_string failed: %s", exc)
        return raw_body

    # No recognisable headers → the agent sent a bare body, already readable.
    if not parsed.keys():
        return _normalise(raw_body)

    rendered = _best_text_from_message(parsed)
    if rendered is None:
        # Couldn't find/decode a text part (e.g. attachment-only, exotic
        # structure) — fall open to the raw payload so the card isn't blank.
        return _normalise(raw_body)

    return _normalise(rendered)


def _normalise(text: str) -> str:
    """Normalise newlines and enforce the rendered-size cap."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > _MAX_RENDERED_CHARS:
        text = text[:_MAX_RENDERED_CHARS] + "\n… (truncated for review)"
    return text
