"""Regression test for SIGNAL_ACTION_MAP moving to rating.py (single source of truth)."""
from tradingagents.agents.utils.rating import SIGNAL_ACTION_MAP


def test_signal_action_map_keys_and_values():
    assert SIGNAL_ACTION_MAP == {
        "Buy": "BUY",
        "Overweight": "BUY",
        "Hold": "HOLD",
        "Underweight": "SELL",
        "Sell": "SELL",
    }


def test_signal_action_map_keys_match_5_tier_vocabulary():
    from tradingagents.agents.utils.rating import RATINGS_5_TIER
    assert set(SIGNAL_ACTION_MAP.keys()) == set(RATINGS_5_TIER)
