#!/usr/bin/env python3
"""Manual test for SofaScore API connectivity"""
import requests
from datetime import datetime

API_BASE = "https://api.sofascore1.com/api/v1"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}

date_str = "2026-07-02"
date_obj = datetime(2026, 7, 2)
timestamp = int(date_obj.timestamp())

endpoints = [
    f"/events/date/{date_str}",
    f"/events/date/{date_str}/",
    f"/events/date/{timestamp}",
    f"/events/date/{date_obj.strftime('%Y%m%d')}",
    f"/events/date/{date_obj.strftime('%Y/%m/%d')}",
    f"/sport/1/events/date/{date_str}",
    f"/sport/1/events/date/{date_str}/",
    f"/tournaments/date/{date_str}",
    f"/matches/date/{date_str}",
    f"/fixtures/date/{date_str}",
    f"/events/{date_str}",
    f"/events/{timestamp}",
    f"/category/1/events/date/{date_str}",
    f"/events/date?date={date_str}",
    f"/events?date={date_str}",
]

for ep in endpoints:
    url = f"{API_BASE}{ep}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        status = resp.status_code
        text = resp.text[:300]
        print(f"URL: {url}")
        print(f"Status: {status}")
        print(f"Response: {text}")
    except Exception as e:
        print(f"Error: {e}")
    print("-" * 50)