import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.state import LatestState
from bot.handlers.commands import _ALL_BOOKMAKER_KEYS, _next_update_note, _selected_bookmakers


class _FakeUser:
    def __init__(self, allowed_bookmakers):
        self.allowed_bookmakers = allowed_bookmakers


def test_next_update_note_counts_down_when_well_before_due():
    state = LatestState()
    state.updated_at = time.time() - 10  # 10s ago, 150s interval -> ~140s left
    note = _next_update_note(state, poll_interval_seconds=150)
    assert note.startswith("~")
    assert "сек" in note


def test_next_update_note_says_soon_when_almost_due():
    state = LatestState()
    state.updated_at = time.time() - 148  # only ~2s of a 150s interval left
    assert _next_update_note(state, poll_interval_seconds=150) == "скоро"


def test_next_update_note_says_soon_when_overdue():
    state = LatestState()
    state.updated_at = time.time() - 999  # cycle running long, well past the interval
    assert _next_update_note(state, poll_interval_seconds=150) == "скоро"


def test_selected_bookmakers_defaults_to_everything():
    assert _selected_bookmakers(_FakeUser([])) == set(_ALL_BOOKMAKER_KEYS)


def test_selected_bookmakers_honors_explicit_list():
    assert _selected_bookmakers(_FakeUser(["fonbet", "olimpbet"])) == {"fonbet", "olimpbet"}
