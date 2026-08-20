# Audit-log external chain-head anchoring (tail-truncation detection)

## The threat this closes

Nuvrail's `audit_log` is a SHA-256 **hash chain**: every row stores the
`entry_hash` of the previous row as its `prev_hash`, and its own `entry_hash`
binds its id + fields. `verify_audit_chain()` walks those links, so **forgery**
(altering a row's fields) and **middle-row deletion** both break a link and are
detected.

There is one deletion the in-DB walk **cannot** catch: **tail truncation** —
deleting the most-recent N rows. The remaining chain is still internally
consistent (it starts from `rows[0].prev_hash` and never learns that later rows
once existed), so `verify_audit_chain()` still returns "intact." An attacker
with DB write access can therefore silently drop the end of the log — exactly
the rows describing their own recent activity.

```
before:  r1 ── r2 ── r3        verify_audit_chain: OK
attack:  r1 ── r2              verify_audit_chain: STILL OK  ← the gap
```

## The fix: anchor the chain head outside the DB

Record the current chain head to an **append-only sink on separate storage**.
Each anchored head is the `entry_hash` of a specific row, cryptographically
bound to that row and (via the `prev_hash` links) every row before it. So a
previously-anchored head can only still appear in the live chain if that exact
row — and its whole prefix — is still present:

```
last anchored head still in live chain  →  tail intact
last anchored head NOT in live chain    →  TAIL TRUNCATION (flagged loudly)
```

`verify_against_anchor()` performs exactly this check; the background
`run_audit_verification_loop` runs it every pass and re-anchors only a head it
just verified intact. Both the loop's ERROR log and the `/audit/verify` endpoint
surface a detected divergence (`anchor_ok: false`, with a reason).

## Enabling it

Anchoring is **OFF by default** — self-hosters are not forced into an external
dependency. Enable it by pointing at a file on append-only / WORM storage:

```bash
export NUVRAIL_AUDIT_ANCHOR_PATH=/mnt/worm/nuvrail/audit-anchor.jsonl
```

- The sink is written **append-only** (`O_APPEND`, one JSON line per anchor:
  `{"anchored_at", "chain_head", "row_count"}`); records are never rewritten.
- For real tamper-evidence the target must be storage the DB attacker **cannot
  also rewrite** — an append-only-mounted volume, a WORM object store, or a
  separate host. Anchoring to the same writable filesystem as the DB provides
  no protection against an attacker who has that filesystem.
- `row_count` is human-diagnostic only; detection relies on the cryptographic
  head, not the count.

## Verifying

`GET /audit/verify` returns, when anchoring is enabled:

| field | meaning |
|-------|---------|
| `ok` | AND of in-DB chain intact **and** no tail truncation |
| `chain_ok` | in-DB hash-chain walk alone |
| `anchor_ok` | tail-truncation check against the last anchor |
| `anchor_reason` | why `anchor_ok` is false (names the missing head) |
| `anchoring_enabled` | whether any anchor has been recorded |
| `last_anchored_at` | timestamp of the most recent anchor |

Run both checks for full coverage: the in-DB walk covers forgery + interior
deletion; the anchor covers the tail.
