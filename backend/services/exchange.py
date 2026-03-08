"""
USD → GBP exchange rate service.
Fetches the live rate from frankfurter.app and caches it for 1 hour.
Falls back to a hard-coded rate if the request fails.
"""

import json
import time
import urllib.request

_FALLBACK_RATE = 0.79          # approximate fallback if network unavailable
_CACHE_TTL = 3600              # 1 hour
_API_URL = "https://api.frankfurter.app/latest?from=USD&to=GBP"

_cache: dict = {
    "rate": None,
    "date": None,
    "expires": 0.0,
}


def get_usd_to_gbp() -> dict:
    """
    Return a dict with keys:
      rate  – float USD→GBP conversion rate
      date  – str  ISO date the rate applies to (e.g. "2025-03-07")
      live  – bool True if fetched from network, False if cached/fallback
    """
    now = time.time()

    # Return cached value if still fresh
    if _cache["rate"] is not None and now < _cache["expires"]:
        return {"rate": _cache["rate"], "date": _cache["date"], "live": True}

    # Attempt to fetch a fresh rate
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"Accept": "application/json", "User-Agent": "RevisionAid/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        rate = float(data["rates"]["GBP"])
        date = data.get("date", "")
        _cache["rate"] = rate
        _cache["date"] = date
        _cache["expires"] = now + _CACHE_TTL
        return {"rate": rate, "date": date, "live": True}

    except Exception:
        # Network error – return cached value or hard-coded fallback
        if _cache["rate"] is not None:
            return {"rate": _cache["rate"], "date": _cache["date"], "live": False}
        return {"rate": _FALLBACK_RATE, "date": "", "live": False}
