import requests

API_BASE = "https://api.sofascore1.com/api/v1"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

endpoints = [
    "/events/today",
    "/events/upcoming",
    "/events/live",
    "/search/events/Real%20Madrid",
    "/events/search/Real%20Madrid",
    "/teams/2829/events",
    "/tournaments/1/events",
    "/events/hot",
    "/events/featured",
    "/scheduled",
    "/matches",
    "/fixtures",
]

for ep in endpoints:
    url = f"{API_BASE}{ep}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        status = resp.status_code
        text = resp.text[:400]
        print(f"URL: {url}")
        print(f"Status: {status}")
        print(f"Response: {text}")
    except Exception as e:
        print(f"Error: {e}")
