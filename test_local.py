import urllib.request
import json

url = "http://127.0.0.1:8000/api/v2/analytics/students/14/summary"
req = urllib.request.Request(url, headers={"Authorization": "Bearer dummy"})
try:
    with urllib.request.urlopen(req) as response:
        print("Status:", response.status)
        print(response.read().decode())
except urllib.error.HTTPError as e:
    print("Error:", e.code)
    print(e.read().decode())
