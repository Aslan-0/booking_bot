"""Router assembly. All routers are connected here."""

from aiogram import Router

from handlers import admin, booking, errors, my_bookings, start


def setup_routers() -> Router:
    """Build the root router with all feature routers included.

    Order matters: `start` contains a catch-all fallback and goes last.
    """
    root = Router(name="root")
    root.include_routers(
        errors.router,
        admin.router,
        booking.router,
        my_bookings.router,
        start.router,
    )
    return root
