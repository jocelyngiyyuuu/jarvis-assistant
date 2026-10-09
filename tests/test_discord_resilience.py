import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import jarvis


class DiscordResilienceTests(unittest.TestCase):
    def test_supervisor_retries_transient_disconnect(self):
        async def run():
            with patch.object(
                jarvis,
                "start_discord_bot",
                AsyncMock(side_effect=[True, False]),
            ) as start, patch.object(
                jarvis.asyncio, "sleep", AsyncMock()
            ) as sleep:
                await jarvis.supervise_discord_bot()

            self.assertEqual(start.await_count, 2)
            sleep.assert_awaited_once_with(
                jarvis.DISCORD_RECONNECT_DELAY_SECONDS
            )

        asyncio.run(run())

    def test_supervisor_survives_unexpected_connection_error(self):
        async def run():
            with patch.object(
                jarvis,
                "start_discord_bot",
                AsyncMock(side_effect=[OSError("DNS unavailable"), False]),
            ) as start, patch.object(
                jarvis.asyncio, "sleep", AsyncMock()
            ) as sleep:
                await jarvis.supervise_discord_bot()

            self.assertEqual(start.await_count, 2)
            sleep.assert_awaited_once_with(
                jarvis.DISCORD_RECONNECT_DELAY_SECONDS
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
