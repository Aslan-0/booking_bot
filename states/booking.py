"""FSM states of the booking flow."""

from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    """Service -> date -> time -> confirmation."""

    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    confirming = State()
