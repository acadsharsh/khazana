"""Service layer - all business logic lives here, decoupled from Telegram.

Services accept an :class:`sqlalchemy.ext.asyncio.AsyncSession` so they remain
trivially testable and free of I/O side effects beyond the database.
"""
