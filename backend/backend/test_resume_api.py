import requests

# We will just see if the endpoint returns 404 or 401
url = "http://localhost:8000/api/resume-builder/12/sync-to-profile"
headers = {"Authorization": "Bearer mock"}

response = requests.post(url, headers=headers)
print(f"Status Code: {response.status_code}")
print(f"Response: {response.text}")
