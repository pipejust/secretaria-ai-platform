import requests
import json
import time

url = "http://127.0.0.1:8000/api/sessions/3/dispatch_emails"
payload = {
    "action_item_ids": [3] # Same ID the user reported testing
}
headers = {'Content-Type': 'application/json'}

try:
    print(f"Testing {url} ...")
    response = requests.post(url, json=payload, headers=headers)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
