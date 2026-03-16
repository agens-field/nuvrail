"""
IMAP command line parser.

Extracts tag, command, and arguments from raw IMAP lines.
Handles UID prefix, literals ({n+}), and multi-line commands.

Sub-milestone: 0.2
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedCommand:
    tag: str
    command: str
    uid: bool = False
    args: list[str] = field(default_factory=list)
    raw: str = ""


def parse_line(line: str) -> Optional[ParsedCommand]:
    """Parse a single IMAP command line. Returns None if line is incomplete."""
    # TODO: implement in sub-milestone 0.2
    raise NotImplementedError
