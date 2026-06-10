# Provider IMAP Guide

_Internal reference for provider-specific IMAP quirks and normalization rules implemented in `gateway/provider_profiles.py`._

---

## Overview

AI agents send standard RFC 3501 IMAP commands and must not need to know which mail provider is behind the Nuvrail proxy. This guide documents per-provider quirks and how they are normalized.

Provider detection is done at connection time by matching the agent credential's `upstream_host` against known hostname suffixes (see `gateway/provider_profiles.py`, `_DETECTION_TABLE`).

---

## Gmail (`imap.gmail.com`, `smtp.gmail.com`)

### Archive operation

Gmail does not use standard folder-based archiving. Archive = move to `[Gmail]/All Mail`.

AI agents may issue the classic IMAP archive sequence:

```
UID COPY {uids} [Gmail]/All Mail
UID STORE {uids} +FLAGS (\Deleted)
UID EXPUNGE {uids}   # blocked by proxy
```

**Normalization:** The proxy detects COPY-to-All-Mail immediately followed by STORE `\Deleted` for the same UIDs and rewrites both as a single staged `UID MOVE {uids} [Gmail]/All Mail`. The staged operation carries `intent_label="archive"` (see `gateway/intent.py`) and is described to the user as an Archive (e.g. `Archive 5 messages from news@example.com`), not as a raw MOVE. The rewrite itself is logged as `"REWRITE archive: COPY+STORE(\Deleted) → UID MOVE ..."`.

### Delete to Trash

```
UID COPY {uids} [Gmail]/Trash
UID STORE {uids} +FLAGS (\Deleted)
UID EXPUNGE {uids}   # blocked by proxy
```

**Normalization:** Same as archive, rewritten as `UID MOVE {uids} [Gmail]/Trash` with `intent_label="delete"`. Delete-intent operations are marked urgent in the approval UI, the same as `STORE \Deleted` trash ops.

### Mark as spam

A COPY to `[Gmail]/Spam` followed by STORE `\Deleted` is recognized the same way and rewritten as `UID MOVE {uids} [Gmail]/Spam` with `intent_label="mark_spam"`. A direct `UID MOVE` to the spam folder gets the same label; moving a message out of `[Gmail]/Spam` back to `INBOX` is labeled `not_spam`.

### Sent Mail deduplication

Gmail automatically adds a copy to `[Gmail]/Sent Mail` after every successful SMTP send. If the AI agent also APPENDs to `[Gmail]/Sent Mail`, the message appears twice in Sent.

**Normalization:** APPEND commands targeting `[Gmail]/Sent Mail` are silently suppressed at the proxy layer. The agent receives `OK APPEND completed` but no operation is staged or executed. Logged as `"SUPPRESSED APPEND to '[Gmail]/Sent Mail' (sent-dedup: Gmail provider)"`.

### MOVE capability

Gmail supports RFC 6851 `UID MOVE`. All staged move operations targeting Gmail are executed as `UID MOVE` on approval (not `COPY + STORE \Deleted + EXPUNGE`).

---

## Outlook / Microsoft 365 (`outlook.office365.com`, `hotmail.com`, `live.com`)

### Trash folder

Outlook uses `Deleted Items` rather than `\Trash` or `[Gmail]/Trash`.

**Normalization:** When staging `trash`-type operations for Outlook agents, `folder_to` is set to `Deleted Items`.

### Sent Mail deduplication

Outlook similarly auto-adds to `Sent Items` after SMTP send. APPEND to `Sent Items` or `Sent` is suppressed.

### MOVE capability

Outlook/Exchange supports RFC 6851 `UID MOVE`.

---

## Generic IMAP

No normalization beyond standard RFC 3501. `UID MOVE` is not assumed.

---

## Adding a new provider

1. Add a `ProviderProfile` constant to `gateway/provider_profiles.py`
2. Add a `(hostname_suffix, profile)` entry to `_DETECTION_TABLE`
3. Update this document
4. Add tests to `tests/gateway/test_provider_profiles.py`
