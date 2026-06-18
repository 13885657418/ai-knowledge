import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sqlmodel import select

from app.tests_pre_start import init, logger


def test_init_successful_connection() -> None:
    engine_mock = MagicMock()
    connection_mock = AsyncMock()
    connect_context = AsyncMock()
    connect_context.__aenter__.return_value = connection_mock
    engine_mock.connect.return_value = connect_context

    select1 = select(1)

    with (
        patch("app.tests_pre_start.select", return_value=select1),
        patch.object(logger, "info"),
        patch.object(logger, "error"),
        patch.object(logger, "warn"),
    ):
        try:
            asyncio.run(init(engine_mock))
            connection_successful = True
        except Exception:
            connection_successful = False

        assert connection_successful, (
            "The database connection should be successful and not raise an exception."
        )

        connection_mock.execute.assert_awaited_once_with(select1)
