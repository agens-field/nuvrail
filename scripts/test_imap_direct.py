"""
Quick diagnostic: connects directly to imap.gmail.com and tests
AUTHENTICATE XOAUTH2, reading ALL response lines (not just one).

Usage inside the gateway container:
  python3 /tmp/test_imap_direct.py <agent_username>

Shows exactly what Gmail sends after AUTHENTICATE XOAUTH2 —
without the proxy in the middle. This isolates whether the
failure is in the proxy or at Gmail.
"""
import asyncio
import os
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DB = Path(os.environ.get("NUVRAIL_DB_PATH", "/data/nuvrail.db"))

async def main():
    if len(sys.argv) < 2:
        print("Usage: test_imap_direct.py <agent_username>")
        sys.exit(1)

    agent_username = sys.argv[1]

    from gateway.oauth2_tokens import get_xoauth2_string
    from gateway.state_db import get_db

    # Look up the agent's integer id
    async with get_db(DB) as db, db.execute(
        "SELECT id, upstream_user FROM agent_credentials WHERE agent_username = ?",
        (agent_username,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        print(f"ERROR: agent {agent_username!r} not found in DB")
        sys.exit(1)

    agent_id = str(dict(row)["id"])
    upstream_user = dict(row)["upstream_user"]
    print(f"Agent id       : {agent_id}")
    print(f"Upstream user  : {upstream_user}")

    print("\nFetching XOAUTH2 token...")
    xoauth2_str = await get_xoauth2_string(agent_id, DB)
    print("Token obtained : OK (not shown)")

    print("\nConnecting to imap.gmail.com:993 ...")
    ssl_ctx = ssl.create_default_context()
    reader, writer = await asyncio.open_connection("imap.gmail.com", 993, ssl=ssl_ctx)

    # Read ALL lines until we see nothing for 2s (greeting may be multi-line)
    print("\n--- IMAP greeting ---")
    greeting_lines = []
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=3.0)
            greeting_lines.append(line)
            print(f"  S: {line!r}")
            if line.startswith((b"* OK", b"* BYE")):
                break
        except TimeoutError:
            break

    # Send AUTHENTICATE XOAUTH2
    tag = "X001"
    auth_cmd = f"{tag} AUTHENTICATE XOAUTH2 {xoauth2_str}\r\n".encode()
    print(f"\n--- Sending: {tag} AUTHENTICATE XOAUTH2 <token> ---")
    writer.write(auth_cmd)
    await writer.drain()

    # Read ALL response lines (not just one)
    print("\n--- Gmail response (all lines) ---")
    resp_lines = []
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            resp_lines.append(line)
            print(f"  S: {line!r}")
            # Stop at tagged response or SASL challenge
            if line.startswith((f"{tag}".encode(), b"+ ")):
                # If SASL challenge, send empty response
                if line.startswith(b"+ "):
                    print("  C: (sending empty SASL response)")
                    writer.write(b"\r\n")
                    await writer.drain()
                    # Read final NO
                    final = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    resp_lines.append(final)
                    print(f"  S: {final!r}")
                break
        except TimeoutError:
            print("  (timeout waiting for more lines)")
            break

    print("\n--- Summary ---")
    print(f"Total lines from Gmail: {len(resp_lines)}")
    final_resp = resp_lines[-1] if resp_lines else b""
    if b" OK " in final_resp.upper():
        print("Result: SUCCESS (OK in final line)")
    elif final_resp[:2] == b"+ ":
        print("Result: SASL CHALLENGE (auth failed)")
    else:
        print(f"Result: UNEXPECTED — {final_resp!r}")
        if len(resp_lines) > 1:
            print(f"NOTE: Proxy reads only line 1 as login_resp: {resp_lines[0]!r}")
            print("      If that line lacks ' OK ', proxy fails even though final line is OK.")

    writer.close()

asyncio.run(main())
