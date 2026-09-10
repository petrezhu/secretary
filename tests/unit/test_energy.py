"""Unit tests for energy state — extended with compute() scoring tests."""

from secretary.coach.energy import EnergyState

# ── Original greeting_tone tests ──────────────────────────────────────────


def test_energy_high():
    state = EnergyState(score=85)
    assert "状态很棒" in state.greeting_tone()


def test_energy_medium():
    state = EnergyState(score=65)
    assert "状态不错" in state.greeting_tone()


def test_energy_low():
    state = EnergyState(score=30)
    assert "简单的事" in state.greeting_tone()


def test_energy_very_low():
    state = EnergyState(score=15)
    assert "照顾好自己" in state.greeting_tone()


def test_energy_boundary_80():
    assert "状态很棒" in EnergyState(80).greeting_tone()


def test_energy_boundary_79():
    assert "状态不错" in EnergyState(79).greeting_tone()


# ── compute() factory tests ───────────────────────────────────────────────


def test_compute_default():
    """Default parameters should produce a mid-range score."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=10)
    # base(50) + 0.5*30(15) - 0 + time_bonus(10) = 75
    assert state.score == 75.0


def test_compute_high_completion_morning():
    """High completion + morning = high energy."""
    state = EnergyState.compute(completion_rate=1.0, consecutive_failures=0, hour=8)
    # base(50) + 30 - 0 + 10 = 90
    assert state.score == 90.0


def test_compute_low_completion_many_failures():
    """Low completion + many failures = low energy."""
    state = EnergyState.compute(completion_rate=0.1, consecutive_failures=6, hour=23)
    # base(50) + 3 - 25(min capped) + (-10) = 18
    assert state.score == 18.0


def test_compute_clamp_high():
    """Score should be clamped to 100."""
    state = EnergyState.compute(completion_rate=1.0, consecutive_failures=0, hour=8)
    assert state.score <= 100.0


def test_compute_clamp_low():
    """Score should be clamped to 0."""
    state = EnergyState.compute(completion_rate=0.0, consecutive_failures=10, hour=3)
    # base(50) + 0 - 25(min capped) + (-10) = 15
    assert state.score >= 0.0


def test_compute_failure_penalty_cap():
    """Failure penalty should be capped at 25."""
    state1 = EnergyState.compute(completion_rate=0.5, consecutive_failures=5, hour=10)
    state2 = EnergyState.compute(completion_rate=0.5, consecutive_failures=10, hour=10)
    # Both should have same penalty (25 cap)
    assert state1.score == state2.score


# ── Time-of-day bonus tests ──────────────────────────────────────────────


def test_time_morning_peak():
    """Morning (6-10) should give +10 bonus."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=8)
    assert state.score == 75.0  # 50 + 15 + 10


def test_time_midday():
    """Midday (11-14) should give +5 bonus."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=12)
    assert state.score == 70.0  # 50 + 15 + 5


def test_time_afternoon():
    """Afternoon (15-18) should give 0 bonus."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=16)
    assert state.score == 65.0  # 50 + 15 + 0


def test_time_evening():
    """Evening (19-22) should give -5 bonus."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=20)
    assert state.score == 60.0  # 50 + 15 - 5


def test_time_late_night():
    """Late night (23-5) should give -10 bonus."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=2)
    assert state.score == 55.0  # 50 + 15 - 10


def test_time_boundary_6():
    """Hour 6 should be morning peak."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=6)
    assert state.score == 75.0


def test_time_boundary_5():
    """Hour 5 should be late night."""
    state = EnergyState.compute(completion_rate=0.5, consecutive_failures=0, hour=5)
    assert state.score == 55.0


# ── Greeting tone thresholds ─────────────────────────────────────────────


def test_greeting_boundary_40():
    assert "放轻松" in EnergyState(40).greeting_tone()


def test_greeting_boundary_39():
    assert "简单的事" in EnergyState(39).greeting_tone()


def test_greeting_boundary_20():
    assert "简单的事" in EnergyState(20).greeting_tone()


def test_greeting_boundary_19():
    assert "照顾好自己" in EnergyState(19).greeting_tone()


def test_greeting_boundary_60():
    assert "状态不错" in EnergyState(60).greeting_tone()


def test_greeting_boundary_59():
    assert "放轻松" in EnergyState(59).greeting_tone()


def test_score_clamp_negative():
    """Negative input should be clamped to 0."""
    state = EnergyState(-10)
    assert state.score == 0.0


def test_score_clamp_over_100():
    """Score over 100 should be clamped."""
    state = EnergyState(150)
    assert state.score == 100.0
