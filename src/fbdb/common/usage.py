"""Daily usage counter and hard cap for paid APIs (Pappers credits, Google Places calls).

reserve() is a single atomic upsert: it adds `credits` to today's row only if the total stays within `cap`,
so two concurrent runs can never overshoot. Reserve BEFORE the paid call; call release() when the call
failed before the provider could charge for it (network error), so retries do not eat the cap.
"""
from __future__ import annotations


def reserve(conn, provider: str, cap: int, credits: int = 1) -> bool:
    """True when `credits` were reserved for today; False when that would exceed `cap` (nothing is written)."""
    if credits > cap:
        return False
    row = conn.execute(
        """INSERT INTO api_usage (provider, calls, credits) VALUES (%(p)s, 1, %(c)s)
           ON CONFLICT (usage_date, provider) DO UPDATE
             SET calls = api_usage.calls + 1, credits = api_usage.credits + %(c)s
             WHERE api_usage.credits + %(c)s <= %(cap)s
           RETURNING credits""",
        {"p": provider, "c": credits, "cap": cap}).fetchone()
    conn.commit()
    return row is not None


def release(conn, provider: str, credits: int = 1) -> None:
    conn.execute(
        """UPDATE api_usage SET calls = greatest(calls - 1, 0), credits = greatest(credits - %s, 0)
           WHERE provider = %s AND usage_date = (now() AT TIME ZONE 'utc')::date""", (credits, provider))
    conn.commit()


def used_today(conn, provider: str) -> tuple[int, int]:
    """(calls, credits) already used today (UTC)."""
    r = conn.execute("SELECT calls, credits FROM api_usage WHERE provider=%s AND usage_date=(now() AT TIME ZONE 'utc')::date",
                     (provider,)).fetchone()
    return (r[0], r[1]) if r else (0, 0)
