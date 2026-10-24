"""Inventory (treats), stat history samples and mood-modulated decay (FR-1.3)."""

import json
import time

import pytest

from minibot.config import DecayRates
from minibot.state import MAX_TREATS, BotError, BotStore


# -- inventory ---------------------------------------------------------------

def test_actions_award_treats(store):
    store.feed()
    assert store.state.treats == 1
    store.rest()
    assert store.state.treats == 2


def test_playing_awards_more_treats(store):
    store.play()
    assert store.state.treats == 2


def test_status_reports_treats(store):
    assert store.status()["treats"] == 0
    store.feed()
    assert store.status()["treats"] == 1


def test_give_treat_spends_one(store):
    store.feed()
    before = store.state.happiness
    outcome = store.give_treat()
    assert outcome["ok"] is True
    assert store.state.treats == 0
    assert store.state.happiness == pytest.approx(min(100.0, before + 12), abs=0.01)


def test_give_treat_without_inventory_raises(store):
    with pytest.raises(BotError, match="no treats"):
        store.give_treat()


def test_treats_are_capped(store):
    store.state.treats = MAX_TREATS - 1
    store.feed()
    assert store.state.treats == MAX_TREATS


def test_treat_inventory_persists(store, config):
    store.feed()
    again = BotStore(config.state_path, config.decay)
    assert again.state.treats == 1


def test_give_treat_tool_via_toolset(toolset):
    toolset.execute("feed_bot", {})
    outcome = toolset.execute("give_treat", {})
    assert outcome["ok"] is True
    assert "treat" in outcome["message"]


def test_give_treat_tool_error_when_empty(toolset):
    outcome = toolset.execute("give_treat", {})
    assert outcome["ok"] is False
    assert "no treats" in outcome["error"]


# -- stat history ------------------------------------------------------------

def test_tick_records_samples(store):
    start = time.time()
    store.tick(now=start + 3600)
    store.tick(now=start + 2 * 3600)
    samples = store.stats_history()
    assert len(samples) == 2
    assert set(samples[0]) == {"t", "hunger", "happiness", "energy", "health"}
    assert samples[0]["hunger"] == pytest.approx(48, abs=0.01)


def test_stats_history_respects_limit(store):
    start = time.time()
    for hour in range(1, 5):
        store.tick(now=start + hour * 3600)
    assert len(store.stats_history(2)) == 2
    assert store.stats_history(2)[-1]["t"] >= store.stats_history(2)[0]["t"]


def test_samples_persist_across_instances(store, config):
    store.tick(now=time.time() + 3600)
    again = BotStore(config.state_path, config.decay)
    assert again.stats_history()


def test_corrupt_samples_reset(store):
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["samples"] = "not-a-list"
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    again = BotStore(store.path, store.rates)
    assert again.stats_history() == []


def test_malformed_samples_dropped(store):
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["samples"] = [
        {"t": 1, "hunger": 50, "happiness": 50, "energy": 50, "health": 50},
        "junk",
        {"t": 2},  # missing stats
        {"t": "soon", "hunger": 1, "happiness": 1, "energy": 1, "health": 1},
    ]
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    again = BotStore(store.path, store.rates)
    assert len(again.stats_history()) == 1


# -- mood-modulated decay ----------------------------------------------------

def _fresh(rates: DecayRates) -> BotStore:
    import tempfile

    return BotStore(f"{tempfile.mkdtemp()}/state.json", rates)


def test_default_decay_unchanged_for_a_normal_bot():
    """Guard: the baseline rates must stay exact when the bot is fine."""
    store = _fresh(DecayRates())
    store.tick(now=time.time() + 3600)
    assert store.state.hunger == pytest.approx(48, abs=0.01)
    assert store.state.energy == pytest.approx(72, abs=0.01)
    assert store.state.happiness == pytest.approx(64, abs=0.01)


def test_starving_drains_happiness_faster():
    rates = DecayRates(hunger_per_hour=0, happiness_per_hour=6, energy_per_hour=0,
                       health_damage_per_hour=0)
    store = _fresh(rates)
    store.state.hunger = 90
    store.tick(now=time.time() + 3600)
    # base 6 + 4 extra for starving
    assert store.state.happiness == pytest.approx(70 - 10, abs=0.01)


def test_sick_bot_loses_happiness_faster():
    rates = DecayRates(hunger_per_hour=0, happiness_per_hour=6, energy_per_hour=0,
                       health_damage_per_hour=0)
    store = _fresh(rates)
    store.state.health = 20
    store.tick(now=time.time() + 3600)
    assert store.state.happiness == pytest.approx(70 - 9, abs=0.01)


def test_thriving_bot_cheers_up():
    rates = DecayRates(hunger_per_hour=0, happiness_per_hour=6, energy_per_hour=0,
                       health_regen_per_hour=0)
    store = _fresh(rates)
    store.state.hunger = 10
    store.state.energy = 80
    store.state.health = 100
    store.tick(now=time.time() + 3600)
    # base -6, then +3 for thriving
    assert store.state.happiness == pytest.approx(70 - 6 + 3, abs=0.01)
