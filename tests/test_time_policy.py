"""Tests for time policy module."""

import pytest
from datetime import datetime, timezone
from validator.time_policy import TimePolicy


def test_time_policy_recorded_mode():
    """Test recorded mode captures timestamp once."""
    policy = TimePolicy(mode="recorded")

    # First call captures time
    ts1 = policy.get_timestamp()

    # Second call returns same time
    ts2 = policy.get_timestamp()

    assert ts1 == ts2
    assert ts1.endswith('Z') or '+' in ts1


def test_time_policy_frozen_mode_default():
    """Test frozen mode with default time."""
    policy = TimePolicy(mode="frozen")

    # Should return same frozen time
    ts1 = policy.get_timestamp()
    ts2 = policy.get_timestamp()

    assert ts1 == ts2


def test_time_policy_frozen_mode_explicit():
    """Test frozen mode with explicit timestamp."""
    fixed_time = "2026-02-07T12:00:00+00:00"
    policy = TimePolicy(mode="frozen", frozen_time=fixed_time)

    ts = policy.get_timestamp()
    assert ts == fixed_time


def test_time_policy_invalid_mode():
    """Test that invalid mode raises error."""
    with pytest.raises(ValueError, match="Invalid time policy mode"):
        TimePolicy(mode="invalid")


def test_time_policy_reset_recorded():
    """Test resetting recorded timestamp."""
    policy = TimePolicy(mode="recorded")

    # Get first timestamp
    ts1 = policy.get_timestamp()

    # Reset
    policy.reset_recorded()

    # Get new timestamp (should be different)
    ts2 = policy.get_timestamp()

    # Both should be valid ISO timestamps
    assert ts1.endswith('Z') or '+' in ts1
    assert ts2.endswith('Z') or '+' in ts2


def test_time_policy_reset_frozen_no_effect():
    """Test that reset has no effect in frozen mode."""
    policy = TimePolicy(mode="frozen")

    ts1 = policy.get_timestamp()
    policy.reset_recorded()
    ts2 = policy.get_timestamp()

    # Should be unchanged
    assert ts1 == ts2


def test_time_policy_from_config(tmp_path):
    """Test loading time policy from config file."""
    # Create temporary config file
    config_file = tmp_path / "test_core.yaml"
    config_file.write_text("""
time_policy:
  mode: recorded
""")

    policy = TimePolicy.from_config(str(config_file))

    assert policy.mode == "recorded"
    ts = policy.get_timestamp()
    assert ts is not None


def test_time_policy_from_config_frozen(tmp_path):
    """Test loading frozen time policy from config."""
    config_file = tmp_path / "test_core.yaml"
    config_file.write_text("""
time_policy:
  mode: frozen
  frozen_time: "2026-01-01T00:00:00+00:00"
""")

    policy = TimePolicy.from_config(str(config_file))

    assert policy.mode == "frozen"
    ts = policy.get_timestamp()
    assert ts == "2026-01-01T00:00:00+00:00"


def test_time_policy_from_config_defaults(tmp_path):
    """Test loading config with missing time_policy section."""
    config_file = tmp_path / "test_core.yaml"
    config_file.write_text("""
system:
  python_version: "3.12"
""")

    policy = TimePolicy.from_config(str(config_file))

    # Should default to recorded
    assert policy.mode == "recorded"
