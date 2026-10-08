import httpx
import asyncio

async def main():
    async with httpx.AsyncClient() as c:
        r = await c.get('http://127.0.0.1:8000/api/v2/analytics/colleges/Barabari/practice-metrics')
        print(r.status_code)
        print(r.text)

if __name__ == "__main__":
    asyncio.run(main())
