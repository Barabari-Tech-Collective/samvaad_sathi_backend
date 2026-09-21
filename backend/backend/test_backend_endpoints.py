import asyncio
from fastapi.testclient import TestClient
from src.main import backend_app

client = TestClient(backend_app)

def test_endpoints():
    response1 = client.get('/api/v2/analytics/colleges/GDC%20Begumpet/practice-metrics', headers={'Authorization': 'Bearer test-admin-token'})
    print('--- Practice Metrics ---')
    print(response1.status_code)
    try:
        print(response1.json())
    except:
        print(response1.text)

    response2 = client.get('/api/v2/analytics/colleges/GDC%20Begumpet/weak-skills', headers={'Authorization': 'Bearer test-admin-token'})
    print('\n--- Weak Skills ---')
    print(response2.status_code)
    try:
        print(response2.json())
    except:
        print(response2.text)

if __name__ == '__main__':
    test_endpoints()
