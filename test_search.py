import requests
API = "https://api.sofascore1.com/api/v1"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/"}
for t in ["Spain", "Austria", "Portugal", "Croatia", "Switzerland", "Algeria", "Cabo Verde", "Cape Verde", "Argentina"]:
    r = requests.get(API + "/search/events?q=" + t + "&page=0", headers=H, timeout=10)
    d = r.json()
    results = d.get("results", [])
    print("\n=== " + t + " (" + str(len(results)) + " results) ===")
    for item in results[:8]:
        e = item.get("entity", {})
        h = e.get("homeTeam", {})
        a = e.get("awayTeam", {})
        s = e.get("status", {}).get("type", "?")
        ts = e.get("startTimestamp", 0)
        print("  ID=" + str(e.get("id")) + " " + h.get("name","?") + "(id=" + str(h.get("id","?")) + ") vs " + a.get("name","?") + "(id=" + str(a.get("id","?")) + ") [" + s + "] ts=" + str(ts))
