"""Bot state tests (FR-1): decay, persistence, actions, moods."""

import json
import time

import pytest

from minibot.config import DecayRates
from minibot.state import BotError, BotStore, clamp


def test_first_run_creates_state(store):
    assert store.path.exists()
    assert store.state.name == "Pixel"
    assert 0 <= store.state.hunger <= 100
    assert store.state.mood in ("happy", "content", "sleepy", "starving", "sad", "sick")


def test_persistence_across_instances(store, config):
    store.feed()
    hunger_after_feed = store.state.hunger
    again = BotStore(config.state_path, config.decay)
    assert again.state.hunger == pytest.approx(hunger_after_feed)


def test_hunger_decays_over_time(store):
    start = time.time()
    store.tick(now=start + 2 * 3600)  # two hours later
    assert store.state.hunger == pytest.approx(30 + 2 * 18, abs=0.01)


def test_energy_and_happiness_decay(store):
    store.tick(now=time.time() + 3600)
    assert store.state.energy == pytest.approx(80 - 8, abs=0.01)
    assert store.state.happiness == pytest.approx(70 - 6, abs=0.01)


def test_stats_clamp_at_bounds(store):
    store.tick(now=time.time() + 100 * 3600)
    assert store.state.hunger == 100.0
    assert store.state.energy == 0.0
    assert store.state.health == 0.0
    assert store.state.mood == "sick"


def test_starving_damages_health():
    rates = DecayRates(hunger_per_hour=20, health_damage_per_hour=10)
    store = _fresh_store(rates)
    store.state.hunger = 90
    store.tick(now=time.time() + 3600)
    assert store.state.health == pytest.approx(90, abs=0.01)


def test_regen_when_fed_and_resting():
    rates = DecayRates(hunger_per_hour=0, energy_per_hour=0, health_regen_per_hour=10)
    store = _fresh_store(rates)
    store.state.health = 50
    store.tick(now=time.time() + 3600)
    assert store.state.health == pytest.approx(60, abs=0.01)


def test_exhaustion_damages_health():
    rates = DecayRates(energy_per_hour=100, health_damage_per_hour=10, hunger_per_hour=0)
    store = _fresh_store(rates)
    store.tick(now=time.time() + 3600)
    assert store.state.energy == 0
    assert store.state.health == pytest.approx(95, abs=0.01)


def test_feed_reduces_hunger(store):
    outcome = store.feed()
    assert outcome["ok"] is True
    assert store.state.hunger == pytest.approx(5, abs=0.01)
    assert "hunger" in outcome["message"]


def test_play_requires_energy(store):
    store.state.energy = 5
    store.save()
    again = BotStore(store.path, store.rates)
    with pytest.raises(BotError, match="too tired"):
        again.play()


def test_play_changes_stats(store):
    outcome = store.play()
    assert outcome["ok"] is True
    assert store.state.happiness == pytest.approx(90, abs=0.01)
    assert store.state.energy == pytest.approx(65, abs=0.01)


def test_rest_restores_energy(store):
    store.state.energy = 10
    store.save()
    again = BotStore(store.path, store.rates)
    again.rest()
    assert again.state.energy == pytest.approx(45, abs=0.01)


def test_mood_priority(store):
    store.state.health = 10
    assert store.state.mood == "sick"
    store.state.health = 100
    store.state.hunger = 95
    assert store.state.mood == "starving"
    store.state.hunger = 10
    store.state.energy = 5
    assert store.state.mood == "sleepy"
    store.state.energy = 50
    store.state.happiness = 10
    assert store.state.mood == "sad"
    store.state.happiness = 90
    assert store.state.mood == "happy"


def test_age_days(store):
    store.state.born_at = time.time() - 3 * 86400 - 10
    assert store.state.age_days() == 3


def test_corrupt_state_recovered(store):
    store.path.write_text("{not json", encoding="utf-8")
    again = BotStore(store.path, store.rates)
    assert again.state.name == "Pixel"
    backups = list(store.path.parent.glob(store.path.name + ".corrupt-*"))
    assert backups, "corrupt file should be preserved as a backup"
    assert json.loads(again.path.read_text(encoding="utf-8"))["version"] == 1


def test_wrongly_typed_values_coerced(store):
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["hunger"] = "not-a-number"
    raw["happiness"] = 250  # out of range
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    again = BotStore(store.path, store.rates)
    assert again.state.hunger == pytest.approx(30)  # back to default
    assert again.state.happiness == 100.0  # clamped


def test_future_timestamps_reanchored(store):
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["last_update"] = time.time() + 99999
    raw["born_at"] = time.time() + 99999
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    again = BotStore(store.path, store.rates)
    assert again.state.age_days() == 0
    assert again.state.hunger == pytest.approx(30)  # no phantom decay applied


def test_clamp():
    assert clamp(-5) == 0.0
    assert clamp(150) == 100.0
    assert clamp(50) == 50.0


def _fresh_store(rates: DecayRates) -> BotStore:
    import tempfile

    tmp = tempfile.mkdtemp()
    return BotStore(f"{tmp}/state.json", rates)
