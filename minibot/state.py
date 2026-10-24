"""Bot state: stats, time-based decay (FR-1), JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import DecayRates

log = logging.getLogger("minibot.state")

MAX_TREATS = 20          # inventory cap — the bot can't hoard forever
MAX_SAMPLES = 240        # stat history kept for /stats charts (~one per tick)


def clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


class BotError(Exception):
    """A bot action cannot be performed right now."""


def _clean_samples(raw) -> list:
    """Keep only well-formed stat samples, newest MAX_SAMPLES of them."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-MAX_SAMPLES:]:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                {
                    "t": float(item["t"]),
                    "hunger": clamp(item["hunger"]),
                    "happiness": clamp(item["happiness"]),
                    "energy": clamp(item["energy"]),
                    "health": clamp(item["health"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


@dataclass
class BotState:
    name: str = "Pixel"
    hunger: float = 30.0    # 0 = full, 100 = starving
    happiness: float = 70.0
    energy: float = 80.0
    health: float = 100.0
    treats: int = 0         # inventory: earned by using tools, spendable with /treat
    born_at: float = 0.0
    last_update: float = 0.0
    samples: list = field(default_factory=list)  # recent stat samples for /stats

    @property
    def mood(self) -> str:
        if self.health < 40:
            return "sick"
        if self.hunger >= 85:
            return "starving"
        if self.energy <= 15:
            return "sleepy"
        if self.happiness < 30:
            return "sad"
        if self.happiness >= 70 and self.energy >= 40:
            return "happy"
        return "content"

    def age_days(self, now: float | None = None) -> int:
        if self.born_at <= 0:
            return 0
        now = time.time() if now is None else now
        return max(0, int((now - self.born_at) // 86400))


class BotStore:
    """Loads, decays, mutates and atomically persists bot state."""

    def __init__(
        self,
        path: str | Path,
        rates: DecayRates | None = None,
        default_name: str = "Pixel",
    ):
        self.path = Path(path).expanduser()
        self.rates = rates or DecayRates()
        self.default_name = default_name
        self.state = self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> BotState:
        now = time.time()
        if not self.path.exists():
            state = BotState(name=self.default_name, born_at=now, last_update=now)
            self._write(state)
            return state
        raw = None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning("state file %s unreadable (%s) — starting fresh", self.path, e)
        if isinstance(raw, dict):
            state = self._coerce_state(raw, now)
            if state is not None:
                return state
        self._backup_corrupt()
        state = BotState(name=self.default_name, born_at=now, last_update=now)
        self._write(state)
        return state

    def _coerce_state(self, raw: dict, now: float) -> BotState | None:
        defaults = BotState(name=self.default_name, born_at=now, last_update=now)
        state = BotState()
        name = raw.get("name", defaults.name)
        state.name = str(name).strip() or defaults.name
        for attr in ("hunger", "happiness", "energy", "health", "born_at", "last_update"):
            value = raw.get(attr, getattr(defaults, attr))
            try:
                setattr(state, attr, float(value))
            except (TypeError, ValueError):
                setattr(state, attr, float(getattr(defaults, attr)))
        for attr in ("hunger", "happiness", "energy", "health"):
            setattr(state, attr, clamp(getattr(state, attr)))
        try:
            state.treats = max(0, int(raw.get("treats", 0)))
        except (TypeError, ValueError):
            state.treats = 0
        state.samples = _clean_samples(raw.get("samples"))
        if state.born_at <= 0 or state.born_at > now + 60:
            state.born_at = now
        if state.last_update <= 0 or state.last_update > now + 60:
            state.last_update = now
        return state

    def _backup_corrupt(self) -> None:
        if not self.path.exists():
            return
        target = self.path.with_name(self.path.name + f".corrupt-{int(time.time())}")
        try:
            self.path.rename(target)
            log.warning("backed up corrupt state to %s", target)
        except OSError as e:
            log.warning("could not back up corrupt state: %s", e)

    def _write(self, state: BotState | None = None) -> None:
        state = state or self.state
        data = dict(asdict(state))
        data["version"] = 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def save(self) -> None:
        self._write()

    # -- decay ---------------------------------------------------------------

    def tick(self, now: float | None = None) -> BotState:
        """Apply time-based decay up to `now` and persist (FR-1.2)."""
        now = time.time() if now is None else now
        s = self.state
        elapsed = now - s.last_update
        if elapsed <= 0:
            if elapsed < 0:  # clock skew: just re-anchor
                s.last_update = now
                self._write(s)
            return s
        hours = elapsed / 3600.0
        r = self.rates
        s.hunger = clamp(s.hunger + r.hunger_per_hour * hours)
        s.happiness = clamp(s.happiness - r.happiness_per_hour * hours)
        s.energy = clamp(s.energy - r.energy_per_hour * hours)
        if s.hunger >= 85:
            s.health = clamp(s.health - r.health_damage_per_hour * hours)
        elif s.energy <= 0:
            s.health = clamp(s.health - r.health_damage_per_hour * 0.5 * hours)
        elif s.hunger <= 60 and s.energy >= 30:
            s.health = clamp(s.health + r.health_regen_per_hour * hours)
        # Mood-modulated drift (FR-1.3): neglect compounds the sadness, and a
        # genuinely thriving bot cheers up on its own. Only kicks in at the
        # extremes, so baseline rates stay exactly as configured.
        extra_drain = 0.0
        if s.hunger >= 85:
            extra_drain += 4.0
        if s.health < 40:
            extra_drain += 3.0
        if s.energy <= 0:
            extra_drain += 2.0
        if extra_drain:
            s.happiness = clamp(s.happiness - extra_drain * hours)
        if s.hunger <= 20 and s.energy >= 70 and s.health >= 60:
            s.happiness = clamp(s.happiness + 3.0 * hours)
        s.last_update = now
        self.record_sample(now)
        self._write(s)
        return s

    def record_sample(self, now: float | None = None) -> dict:
        """Append a stat sample for the /stats history chart."""
        now = time.time() if now is None else now
        s = self.state
        sample = {
            "t": float(now),
            "hunger": round(s.hunger, 2),
            "happiness": round(s.happiness, 2),
            "energy": round(s.energy, 2),
            "health": round(s.health, 2),
        }
        s.samples.append(sample)
        if len(s.samples) > MAX_SAMPLES:
            del s.samples[: len(s.samples) - MAX_SAMPLES]
        return sample

    def stats_history(self, limit: int = 40) -> list[dict]:
        """Most recent stat samples, oldest first (for charting)."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 40
        return list(self.state.samples[-limit:])

    # -- actions -------------------------------------------------------------

    def feed(self, amount: float = 25.0) -> dict:
        self.tick()
        s = self.state
        before = int(round(s.hunger))
        s.hunger = clamp(s.hunger - amount)
        s.happiness = clamp(s.happiness + 4)
        if s.hunger <= 60:
            s.health = clamp(s.health + 3)
        self.award(1)
        self.save()
        return {
            "ok": True,
            "action": "feed_bot",
            "message": f"{s.name} ate — hunger {before} → {int(round(s.hunger))}.",
            "status": self.status(),
        }

    def play(self) -> dict:
        self.tick()
        s = self.state
        if s.health < 30:
            raise BotError(f"{s.name} is too sick to play — let them rest first.")
        if s.energy < 15:
            raise BotError(f"{s.name} is too tired to play — let them rest first.")
        before = int(round(s.happiness))
        s.happiness = clamp(s.happiness + 20)
        s.energy = clamp(s.energy - 15)
        s.hunger = clamp(s.hunger + 4)
        self.award(2)
        self.save()
        return {
            "ok": True,
            "action": "play_with_bot",
            "message": (
                f"{s.name} had fun — happiness {before} → {int(round(s.happiness))}, "
                f"energy {int(round(s.energy))}."
            ),
            "status": self.status(),
        }

    def rest(self) -> dict:
        self.tick()
        s = self.state
        before = int(round(s.energy))
        s.energy = clamp(s.energy + 35)
        s.hunger = clamp(s.hunger + 3)
        if s.hunger <= 60:
            s.health = clamp(s.health + 2)
        self.award(1)
        self.save()
        return {
            "ok": True,
            "action": "rest_bot",
            "message": f"{s.name} recharged — energy {before} → {int(round(s.energy))}.",
            "status": self.status(),
        }

    # -- inventory -----------------------------------------------------------

    def award(self, treats: int = 1) -> int:
        """Earn treats by caring for the bot (capped at MAX_TREATS)."""
        s = self.state
        s.treats = max(0, min(MAX_TREATS, int(s.treats) + int(treats)))
        return s.treats

    def give_treat(self) -> dict:
        """Spend one treat: a small happiness/health/energy bump."""
        self.tick()
        s = self.state
        if s.treats <= 0:
            raise BotError(
                f"no treats left — care for {s.name} (feed/play/rest) to earn some."
            )
        s.treats -= 1
        before = int(round(s.happiness))
        s.happiness = clamp(s.happiness + 12)
        s.health = clamp(s.health + 5)
        s.energy = clamp(s.energy + 3)
        self.save()
        return {
            "ok": True,
            "action": "give_treat",
            "message": (
                f"{s.name} loved the treat — happiness {before} → "
                f"{int(round(s.happiness))} ({s.treats} left)."
            ),
            "status": self.status(),
        }

    def status(self) -> dict:
        s = self.state
        return {
            "name": s.name,
            "mood": s.mood,
            "age_days": s.age_days(),
            "hunger": int(round(s.hunger)),
            "happiness": int(round(s.happiness)),
            "energy": int(round(s.energy)),
            "health": int(round(s.health)),
            "treats": int(s.treats),
        }
