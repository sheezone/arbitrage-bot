from aiogram.fsm.state import State, StatesGroup


class Settings(StatesGroup):
    waiting_bankroll = State()
    waiting_threshold = State()
    # Split into three steps (one number each) rather than one "1000 2.10 2.05" message --
    # fewer typos, no need to remember the order, and each step can validate on its own.
    waiting_calc_bankroll = State()
    waiting_calc_odds_a = State()
    waiting_calc_odds_b = State()
