from aiogram.fsm.state import State, StatesGroup


class Settings(StatesGroup):
    waiting_bankroll = State()
    waiting_threshold = State()
    waiting_calculator = State()
