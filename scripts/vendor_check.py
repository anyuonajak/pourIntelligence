#!/usr/bin/env python3
"""A tiny 'vendor app': call Pour Intelligence with an API key.

    export POUR_API_URL=https://pourintelligence.onrender.com
    export POUR_API_KEY='pi_live_...'
    python scripts/vendor_check.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

import httpx

BASE = os.environ.get("POUR_API_URL", "https://pourintelligence.onrender.com").rstrip("/")
KEY = os.environ.get("POUR_API_KEY")


def main() -> int:
    if not KEY:
        print("Set POUR_API_KEY to a key minted in /admin.", file=sys.stderr)
        return 1
    pour = (datetime.now() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    body = {
        "zip_code": "94612",
        "pour_date": pour.isoformat(timespec="seconds"),
        "mix_design": {"cement_type": "Type_I", "target_psi": 4000, "thickness_inches": 4},
    }
    response = httpx.post(
        f"{BASE}/v1/pour-readiness",
        headers={"X-API-Key": KEY, "Content-Type": "application/json"},
        json=body,
        timeout=45.0,
    )
    print(f"HTTP {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)
    return 0 if response.is_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
