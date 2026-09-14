"""Explicitly advance the background read model in API feature tests."""
from app import leaderboard, leaderboard_snapshot


def publish_ready_board():
    leaderboard_snapshot.request_refresh()
    token = leaderboard_snapshot.claim_refresh()
    assert token is not None, 'another fixture retained the leaderboard writer lease'
    try:
        # Tests which change reputation thresholds must not reuse an earlier cache.
        leaderboard._crown_cache.clear()
        return leaderboard_snapshot.publish_snapshot(
            token, leaderboard._today_kst(), leaderboard.compute_entries()['items'])
    finally:
        leaderboard_snapshot.release_refresh(token, delay_seconds=0)
