import logging

import pytest

from logging_config import _RedactingFilter, redact_protocol_line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("AUTH PLAIN AGFnZW50AHNlY3JldA==", "AUTH PLAIN [REDACTED]"),
        ("auth login dXNlcg==", "auth login [REDACTED]"),
        ("AUTH PLAIN", "AUTH PLAIN"),
        ("AUTH LOGIN", "AUTH LOGIN"),
        ("MAIL FROM:<a@example.com>", "MAIL FROM:<a@example.com>"),
        ("EHLO client.example", "EHLO client.example"),
    ],
)
def test_redact_protocol_line(line, expected):
    assert redact_protocol_line(line) == expected


def test_redacting_filter_redacts_credentials_and_keeps_record():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="C->P: %s",
        args=("AUTH PLAIN abc==",),
        exc_info=None,
    )

    result = _RedactingFilter().filter(record)

    assert result is True
    assert record.getMessage() == "C->P: AUTH PLAIN [REDACTED]"
