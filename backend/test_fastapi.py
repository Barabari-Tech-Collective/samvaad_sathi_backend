import asyncio
from httpx import AsyncClient, ASGITransport
from backend.src.main import initialize_backend_application
import httpx

app = initialize_backend_application()

async def test():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": "Bearer dummytoken"}
        print("Testing /api/v2/analytics/students/557/summary")
        response = await ac.get("/api/v2/analytics/students/557/summary", headers=headers)
        print("Summary Status:", response.status_code)
        if response.status_code == 500:
            print("Summary Error:", response.text)
        else:
            print("Summary Output:", response.json())
        
if __name__ == "__main__":
    asyncio.run(test())
