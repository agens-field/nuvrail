"""
Unit tests for IMAP command line parser.

Covers: basic commands, UID prefix, literals, edge cases.
Tests written against real IMAP session captures.

Sub-milestone: 0.2
"""
import pytest
from gateway.imap_parser import parse_line, ParsedCommand


# TODO: add test cases in sub-milestone 0.2
# Examples:
#   "A001 SELECT INBOX"
#   "A002 UID FETCH 1:* (FLAGS)"
#   "A003 STORE 1 +FLAGS (\\Seen)"
#   "A004 UID MOVE 1:5 [Gmail]/Archive"
#   "A005 EXPUNGE"
