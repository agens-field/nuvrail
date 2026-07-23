"""
IMAP command line parser (Milestone 0.2).

Parses a single IMAP command line (already stripped of CRLF) into a
ParsedCommand dataclass.  Uses a flat state-machine tokenizer — no
recursive descent.

Tokenizer state machine
-----------------------

                    ┌──────────────────────────────────────────────────────┐
                    │                      START                           │
                    │  (skip whitespace; dispatch on first char of token)  │
                    └────┬──────┬──────┬──────┬─────────────────────────  ┘
                         │      │      │      │
                    ' '  │   '"'│  '('│  '{'  │  other
                    skip │      │      │       │   ↓
                         │      ▼      ▼       ▼   ATOM
                         │  QUOTED   PAREN  LITERAL  (reads until whitespace;
                         │  (strip   (keep   (keep    enters BRACKET when '[' found;
                         │   quotes)  parens) as-is)  bracket may nest parens)
                         │      │      │       │
                         └──────┴──────┴───────┘
                                    │
                             emit token, back to START

Sync-literal short-circuit
---------------------------
If the last token of the parsed line matches ``{N}`` (digits only, no ``+``),
the line is incomplete — the client must send literal bytes before the command
is finished.  ``parse_line`` returns ``None`` in this case.

Non-sync literals ``{N+}`` are returned as an ordinary arg token.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ParsedCommand:
    tag: str
    command: str
    uid: bool = False
    args: list[str] = field(default_factory=list)
    raw: str = ""


# Matches a sync literal: {digits} with NO trailing +
_SYNC_LITERAL_RE = re.compile(r"^\{\d+\}$")


def _skip_n_tokens(line: str, n: int) -> str:
    """Return the substring of *line* that follows the first *n* whitespace-delimited tokens."""
    i = 0
    length = len(line)
    count = 0
    while count < n and i < length:
        # skip leading spaces for this token
        while i < length and line[i] == " ":
            i += 1
        # skip token characters
        while i < length and line[i] != " ":
            i += 1
        count += 1
    # skip spaces before the rest
    while i < length and line[i] == " ":
        i += 1
    return line[i:]


def _tokenize_args(s: str) -> list[str]:
    """Tokenize the args portion of an IMAP command using a state machine.

    Rules
    -----
    - Quoted strings ``"..."`` → single token, quotes stripped, backslash escapes honoured.
    - Parenthesised lists ``(...)`` → single token, parens kept, supports nesting.
    - Literal markers ``{N}`` / ``{N+}`` → single token, braces kept.
    - Bracket extensions ``[...]`` → merged into the preceding atom token
      (e.g. ``BODY[HEADER.FIELDS (From Subject)]`` is one token).
    - Atoms → everything else, delimited by spaces.
    """
    tokens: list[str] = []
    i = 0
    n = len(s)

    while i < n:
        c = s[i]

        # ── skip whitespace ──────────────────────────────────────────────────
        if c == " ":
            i += 1
            continue

        # ── quoted string ────────────────────────────────────────────────────
        if c == '"':
            i += 1  # consume opening quote
            buf: list[str] = []
            while i < n:
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(s[i + 1])
                    i += 2
                elif ch == '"':
                    i += 1  # consume closing quote
                    break
                else:
                    buf.append(ch)
                    i += 1
            tokens.append("".join(buf))
            continue

        # ── parenthesised list ───────────────────────────────────────────────
        if c == "(":
            depth = 0
            buf = []
            while i < n:
                ch = s[i]
                buf.append(ch)
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            tokens.append("".join(buf))
            continue

        # ── literal marker  {N} or {N+} ─────────────────────────────────────
        if c == "{":
            buf = ["{"]
            i += 1
            while i < n and s[i] != "}":
                buf.append(s[i])
                i += 1
            if i < n:
                buf.append("}")
                i += 1
            tokens.append("".join(buf))
            continue

        # ── atom (possibly with bracket extension) ───────────────────────────
        buf = []
        while i < n and s[i] != " ":
            ch = s[i]
            if ch == "[":
                # Bracket extension: consume until matching ']', allowing nested parens.
                buf.append("[")
                i += 1
                bracket_depth = 1
                while i < n and bracket_depth > 0:
                    bch = s[i]
                    buf.append(bch)
                    if bch == "[":
                        bracket_depth += 1
                    elif bch == "]":
                        bracket_depth -= 1
                    i += 1
                # After ']', continue atom loop — no break here; next char may be space
            else:
                buf.append(ch)
                i += 1
        tokens.append("".join(buf))

    return tokens


def parse_line(line: str) -> ParsedCommand | None:
    """Parse a single IMAP command line (no trailing CRLF).

    Returns ``None`` if the line ends with a sync literal ``{N}`` — the caller
    must read the literal bytes before the command is complete.

    Non-sync literals ``{N+}`` are included in ``args`` and the command is
    returned normally.
    """
    # Split just enough to identify tag / optional UID / command.
    tokens = line.split()
    if len(tokens) < 2:
        # Bare tag with no command — treat as malformed but don't crash.
        return ParsedCommand(tag=line.strip(), command="", raw=line)

    tag = tokens[0]

    # UID prefix check (case-insensitive, RFC 3501 §6.4.8)
    if len(tokens) >= 3 and tokens[1].upper() == "UID":
        uid = True
        command = tokens[2].upper()
        rest = _skip_n_tokens(line, 3)
    else:
        uid = False
        command = tokens[1].upper()
        rest = _skip_n_tokens(line, 2)

    args = _tokenize_args(rest)

    # Sync-literal detection: last token is {N} with digits only.
    if args and _SYNC_LITERAL_RE.match(args[-1]):
        return None

    return ParsedCommand(tag=tag, command=command, uid=uid, args=args, raw=line)
