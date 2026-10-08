import asyncio
from httpx import AsyncClient, ASGITransport
from src.main import backend_app as app
from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.models.db.user import User

async def override_get_current_admin_user():
    u = User()
    u.id = 1
    u.is_admin = True
    return u

async def override_get_current_user():
    u = User()
    u.id = 1
    u.is_admin = True
    return u

app.dependency_overrides[get_current_admin_user] = override_get_current_admin_user
app.dependency_overrides[get_current_user] = override_get_current_user

async def main():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        endpoints = [
            "/api/v2/analytics/students/557/summary",
            "/api/v2/analytics/students/557/practice-completion",
            "/api/v2/analytics/students/557/score-history",
            "/api/v2/analytics/students/557/speech-vs-knowledge-history",
            "/api/v2/analytics/students/557/skill-averages",
        ]
        for endpoint in endpoints:
            print(f"Testing {endpoint}...")
            response = await client.get(endpoint)
            print(f"Status: {response.status_code}")
            if response.status_code != 200:
                print(response.text)

if __name__ == "__main__":
    asyncio.run(main())
