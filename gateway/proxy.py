"""
IMAP Proxy Server — entry point.

Listens on localhost:1143, accepts AI agent IMAP connections,
and routes commands through the staging engine or upstream passthrough.

Flow:
  AI Agent → proxy.py → command_router.py → upstream.py (read)
                                           → staging.py (write)
"""
import asyncio


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    # TODO: implement in sub-milestone 0.1
    pass


async def main() -> None:
    server = await asyncio.start_server(handle_client, "127.0.0.1", 1143)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
