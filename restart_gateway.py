"""Script to gracefully restart gateway via /api/shutdown."""
import asyncio
import sys


async def main():
    try:
        from httpx import AsyncClient
    except ImportError:
        # Fallback: use urllib which is always available
        from urllib.request import urlopen, Request
        urlopen(Request("http://localhost:18790/api/shutdown", data=b""), timeout=5)
        print("Gateway restart triggered")
        return

    async with AsyncClient(timeout=5.0) as client:
        resp = await client.post("http://localhost:18790/api/shutdown")
        print(f"Gateway restart triggered, status: {resp.status_code}")


if __name__ == "__main__":
    asyncio.run(main())