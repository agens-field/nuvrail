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


# ---------------------------------------------------------------------------
# nuvrail-internal#41 items 1-4: the redaction safety net
# ---------------------------------------------------------------------------
import ast  # noqa: E402
import io  # noqa: E402
from pathlib import Path  # noqa: E402

from logging_config import setup_logging  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Item 1: multi-word AUTH payloads are redacted to end of line.
        ("AUTH XOAUTH2 dXNlcj1h abc", "AUTH XOAUTH2 [REDACTED]"),
        ("AUTH PLAIN part1 part2 part3", "AUTH PLAIN [REDACTED]"),
        ("a2 AUTHENTICATE PLAIN AGFnZW50AHNlY3JldA==", "a2 AUTHENTICATE PLAIN [REDACTED]"),
        ("a2 AUTHENTICATE PLAIN", "a2 AUTHENTICATE PLAIN"),
        # Item 2: quoted IMAP LOGIN passwords containing spaces, quoted users.
        ('a1 LOGIN alice "pa ss"', "a1 LOGIN alice [REDACTED]"),
        ('a1 LOGIN "alice smith" "pa ss word"', 'a1 LOGIN "alice smith" [REDACTED]'),
        ("a1 LOGIN alice secret", "a1 LOGIN alice [REDACTED]"),
        ('a1 login alice "p\\"q r"', "a1 login alice [REDACTED]"),
        ("a1 LOGIN alice", "a1 LOGIN alice"),
        # Non-auth lines are untouched.
        ("a3 SELECT INBOX", "a3 SELECT INBOX"),
    ],
)
def test_redact_protocol_line_full_payload(line, expected):
    assert redact_protocol_line(line) == expected


@pytest.fixture
def fresh_root():
    """Run setup_logging() against an empty root logger, then restore it.

    pytest installs its own capture handlers on the root logger; set them
    aside so the test sees exactly what a real process would.
    """
    root = logging.getLogger()
    saved = (root.handlers[:], root.filters[:], root.level)
    root.handlers = []
    root.filters = []
    stream = io.StringIO()
    try:
        setup_logging()
        assert len(root.handlers) == 1
        root.handlers[0].setStream(stream)
        yield stream
    finally:
        root.handlers, root.filters, root.level = saved[0], saved[1], saved[2]


def test_child_logger_records_are_redacted(fresh_root):
    # Item 3: before the fix the filter sat on the root *logger*, which named
    # loggers bypass. This is the reproduction from #41.
    logging.getLogger("gateway.smtp_proxy").warning("C->P: %s", "AUTH PLAIN SECRETBLOB==")
    logging.getLogger("gateway.proxy").warning("line %s", 'a1 LOGIN alice "pa ss"')
    out = fresh_root.getvalue()
    assert "SECRETBLOB" not in out
    assert "pa ss" not in out
    assert "AUTH PLAIN [REDACTED]" in out
    assert "a1 LOGIN alice [REDACTED]" in out


def test_preformatted_message_is_redacted(fresh_root):
    payload = "AUTH XOAUTH2 dXNlcj1h abc"
    logging.getLogger("api.routes.oauth2").error(f"C->P: {payload}")
    out = fresh_root.getvalue()
    assert "dXNlcj1h" not in out
    assert "C->P: AUTH XOAUTH2 [REDACTED]" in out


def test_format_string_with_auth_words_still_formats(fresh_root):
    # A developer-written format string that happens to say "AUTH PLAIN
    # failed" must not be rewritten, or its %s placeholders disappear and the
    # record fails to format.
    logging.getLogger("gateway.smtp_proxy").warning(
        "[%s] SMTP AUTH PLAIN failed for user=%s", "1.2.3.4", "nuvrail_abc"
    )
    out = fresh_root.getvalue()
    assert "SMTP AUTH PLAIN failed for user=nuvrail_abc" in out
    assert "Logging error" not in out


def test_setup_logging_is_idempotent(fresh_root):
    # pytest adds its own capture handlers to the root during the test phase,
    # so compare before/after rather than asserting an absolute count.
    root = logging.getLogger()
    before = root.handlers[:]
    setup_logging()
    setup_logging()
    assert root.handlers == before
    for handler in root.handlers:
        assert sum(isinstance(f, _RedactingFilter) for f in handler.filters) == 1


@pytest.mark.parametrize(
    ("level_env", "expected"),
    [("debug", logging.DEBUG), ("WARNING", logging.WARNING), ("bogus", logging.INFO)],
)
def test_log_level_env(monkeypatch, level_env, expected):
    monkeypatch.setenv("LOG_LEVEL", level_env)
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    root.handlers = []
    try:
        setup_logging()
        assert root.level == expected
    finally:
        root.handlers, root.level = saved


def _main_block_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    return set()


@pytest.mark.parametrize("module", ["gateway/proxy.py", "gateway/smtp_proxy.py"])
def test_proxy_entry_points_install_redaction(module):
    # Item 4: setup_logging() was never called, so no process had the filter.
    calls = _main_block_calls(REPO / module)
    assert "setup_logging" in calls
    assert "basicConfig" not in (REPO / module).read_text(encoding="utf-8")


def test_api_lifespan_installs_redaction():
    import inspect

    from api.main import lifespan

    assert "setup_logging()" in inspect.getsource(lifespan)
