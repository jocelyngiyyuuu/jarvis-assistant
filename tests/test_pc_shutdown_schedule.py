import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jarvis
from jarvis_core.pc_shutdown_schedule import PcShutdownScheduleStore


class PcShutdownScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = jarvis.CORE.pc_shutdown_schedule
        jarvis.CORE.pc_shutdown_schedule = PcShutdownScheduleStore(
            f"{self.temp_dir.name}/pc_shutdown.sqlite3"
        )

    async def asyncTearDown(self):
        task = jarvis.remote_pc_shutdown_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        jarvis.remote_pc_shutdown_task = None
        jarvis.remote_pc_shutdown_target = None
        jarvis.CORE.pc_shutdown_schedule = self.original_store
        self.temp_dir.cleanup()

    def test_delay_accepts_sleep_compatible_formats(self):
        cases = {
            "tắt PC sau 30p": 30 * 60,
            "hẹn giờ tắt máy tính sau 2h15": 2 * 3600 + 15 * 60,
            "shutdown PC after 45s": 45,
            "đặt lịch tắt máy tính PC sau 1:30": 90 * 60,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(
                    jarvis.parse_remote_pc_shutdown_delay(command), expected
                )

    def test_clock_uses_next_local_occurrence(self):
        now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
        target, seconds = jarvis.parse_remote_pc_shutdown_clock(
            "tắt PC lúc 23:30", now
        )
        self.assertEqual((target.hour, target.minute), (23, 30))
        self.assertEqual(seconds, 3 * 3600 + 30 * 60)

    async def test_route_sets_and_cancels_pc_shutdown_timer(self):
        with patch.object(jarvis, "shutdown_remote_pc") as shutdown:
            handled = await jarvis.route_command(
                "tắt PC sau 30p", allow_local_ai=False
            )
            self.assertTrue(handled)
            self.assertIsNotNone(jarvis.remote_pc_shutdown_task)
            self.assertIn("Đã đặt lịch tắt PC sau 30p", jarvis.last_command_response)
            shutdown.assert_not_called()

            handled = await jarvis.route_command(
                "hủy lịch tắt PC", allow_local_ai=False
            )
            self.assertTrue(handled)
            self.assertIsNone(jarvis.remote_pc_shutdown_task)
            self.assertIn("Đã hủy lịch tắt PC", jarvis.last_command_response)

    async def test_elapsed_timer_shuts_down_pc_in_worker_thread(self):
        with patch.object(jarvis.asyncio, "sleep", new=AsyncMock()), patch.object(
            jarvis.asyncio, "to_thread", new=AsyncMock(return_value=True)
        ) as to_thread:
            await jarvis.remote_pc_shutdown_after_delay(1)
        to_thread.assert_awaited_once_with(jarvis.shutdown_remote_pc)

    async def test_future_schedule_is_restored_after_restart(self):
        now = datetime.now().astimezone()
        target = now + timedelta(hours=1)
        jarvis.CORE.pc_shutdown_schedule.set_pending(target)

        jarvis.restore_remote_pc_shutdown_schedule(now)

        self.assertEqual(jarvis.remote_pc_shutdown_target, target)
        self.assertIsNotNone(jarvis.remote_pc_shutdown_task)
        self.assertEqual(
            jarvis.CORE.pc_shutdown_schedule.get()["status"], "pending"
        )

    async def test_overdue_schedule_is_marked_missed(self):
        now = datetime.now().astimezone()
        jarvis.CORE.pc_shutdown_schedule.set_pending(now - timedelta(minutes=1))

        jarvis.restore_remote_pc_shutdown_schedule(now)

        self.assertIsNone(jarvis.remote_pc_shutdown_task)
        self.assertEqual(
            jarvis.CORE.pc_shutdown_schedule.get()["status"], "missed"
        )

    async def test_failed_ssh_is_recorded(self):
        target = datetime.now().astimezone() + timedelta(seconds=1)
        jarvis.CORE.pc_shutdown_schedule.set_pending(target)
        with patch.object(jarvis.asyncio, "sleep", new=AsyncMock()), patch.object(
            jarvis.asyncio, "to_thread", new=AsyncMock(side_effect=OSError("SSH failed"))
        ):
            await jarvis.remote_pc_shutdown_after_delay(1, target)

        row = jarvis.CORE.pc_shutdown_schedule.get()
        self.assertEqual(row["status"], "failed")
        self.assertIn("SSH failed", row["detail"])

    async def test_status_reports_completed_schedule(self):
        target = datetime.now().astimezone() + timedelta(minutes=1)
        jarvis.CORE.pc_shutdown_schedule.set_pending(target)
        jarvis.CORE.pc_shutdown_schedule.transition(
            target, "pending", "running"
        )
        jarvis.CORE.pc_shutdown_schedule.transition(
            target, "running", "completed"
        )

        handled = await jarvis.handle_remote_pc_shutdown_timer_command(
            "lịch tắt PC"
        )

        self.assertTrue(handled)
        self.assertIn("thành công", jarvis.last_command_response)


if __name__ == "__main__":
    unittest.main()
