import asyncio
from httpx import AsyncClient
from backend.backend.src.main import app
import httpx

async def test():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        print("Testing /api/v2/analytics/students/557/summary")
        response = await ac.get("/api/v2/analytics/students/557/summary")
        print("Summary Status:", response.status_code)
        if response.status_code == 500:
            print("Summary Error:", response.json())
        
        print("Testing /api/v2/analytics/students/557/practice-completion")
        response = await ac.get("/api/v2/analytics/students/557/practice-completion")
        print("Practice Status:", response.status_code)
        if response.status_code == 500:
            print("Practice Error:", response.json())

if __name__ == "__main__":
    asyncio.run(test())
