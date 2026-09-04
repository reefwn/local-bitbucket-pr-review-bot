import asyncio

from mcp.server.fastmcp import FastMCP

from src.mcp_server import tools

PORT = 7390


async def main() -> None:
    mcp = FastMCP("bitbucket-pr", host="0.0.0.0", port=PORT)
    tools.register(mcp)
    await mcp.run_streamable_http_async()


if __name__ == "__main__":
    asyncio.run(main())
