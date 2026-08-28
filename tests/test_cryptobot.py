import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.cryptobot import _format_amount


def test_format_amount_strips_trailing_zeros():
    assert _format_amount(3.5) == "3.5"


def test_format_amount_strips_trailing_point_for_whole_numbers():
    assert _format_amount(78.0) == "78"


def test_format_amount_keeps_significant_decimals():
    assert _format_amount(11.23) == "11.23"
