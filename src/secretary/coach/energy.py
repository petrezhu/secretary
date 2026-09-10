"""Energy state assessment — computes a 0-100 score and greeting tone."""

from __future__ import annotations

from datetime import datetime


class EnergyState:
    """User energy assessment based on task metrics and time of day.

    Scoring formula (0-100):
        base = 50
        + completion_rate contribution (0-30)
        - consecutive_failures penalty (0-25)
        + time_of_day bonus (-10 to +10)

    Usage::

        state = EnergyState.compute(
            completion_rate=0.8,
            consecutive_failures=0,
            hour=9,
        )
        print(state.score, state.greeting_tone())
    """

    def __init__(self, score: float):
        self.score = max(0.0, min(100.0, score))

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def compute(
        cls,
        completion_rate: float = 0.5,
        consecutive_failures: int = 0,
        hour: int | None = None,
    ) -> EnergyState:
        """Compute energy score from task metrics and time of day.

        Parameters
        ----------
        completion_rate:
            Ratio of completed tasks to total tasks (0.0–1.0).
        consecutive_failures:
            Number of tasks failed in a row recently.
        hour:
            Current hour (0-23).  If *None*, uses ``datetime.now().hour``.

        Returns
        -------
        EnergyState
            A new instance with the computed score.
        """
        if hour is None:
            hour = datetime.now().hour

        # Base energy
        base = 50.0

        # Completion contribution: 0 → +0, 1.0 → +30
        completion_bonus = completion_rate * 30.0

        # Failure penalty: each consecutive failure costs 5, capped at 25
        failure_penalty = min(consecutive_failures * 5.0, 25.0)

        # Time-of-day modifier
        # Morning peak (6-10): +10, midday (11-14): +5, afternoon (15-18): 0,
        # evening (19-22): -5, late night (23-5): -10
        if 6 <= hour <= 10:
            time_bonus = 10.0
        elif 11 <= hour <= 14:
            time_bonus = 5.0
        elif 15 <= hour <= 18:
            time_bonus = 0.0
        elif 19 <= hour <= 22:
            time_bonus = -5.0
        else:
            time_bonus = -10.0

        score = base + completion_bonus - failure_penalty + time_bonus
        return cls(score)

    # ── Greeting ────────────────────────────────────────────────────────

    def greeting_tone(self) -> str:
        """Return a context-appropriate Chinese greeting based on energy level."""
        if self.score >= 80:
            return "今天状态很棒！"
        elif self.score >= 60:
            return "状态不错。"
        elif self.score >= 40:
            return "今天节奏放轻松一点。"
        elif self.score >= 20:
            return "今天做点简单的事就好。"
        else:
            return "今天优先照顾好自己。"
