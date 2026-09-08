import unittest
import base64
import gzip
import re
from unittest.mock import AsyncMock, Mock, patch

import jarvis
from jarvis_core.windows_audio import (
    build_windows_audio_command,
    parse_windows_audio_output,
)


class WindowsAudioCommandTests(unittest.TestCase):
    def test_pc_word_is_required(self):
        self.assertIsNone(jarvis.parse_remote_pc_volume_command("tăng âm lượng"))
        self.assertIsNone(jarvis.parse_remote_pc_volume_command("âm lượng hiện tại"))

    def test_pc_volume_commands_are_parsed(self):
        cases = {
            "âm lượng PC hiện tại": ("get", None),
            "âm lượng hiện tại pc": ("get", None),
            "tăng âm lượng PC": ("change", 5),
            "tăng âm lượng 10 pc": ("change", 10),
            "giảm âm lượng pc 7": ("change", -7),
            "âm lượng PC 50": ("set", 50),
            "đặt âm lượng 120 pc": ("set", 100),
            "tắt tiếng PC": ("mute", None),
            "bật tiếng pc": ("unmute", None),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(
                    jarvis.parse_remote_pc_volume_command(command), expected
                )

    def test_compressed_powershell_contains_only_validated_change(self):
        command = build_windows_audio_command("change", -10)
        self.assertEqual(command[:2], ["powershell.exe", "-NoLogo"])
        self.assertEqual(command[-2], "-Command")
        payload = re.search(r"FromBase64String\('([^']+)'\)", command[-1]).group(1)
        script = gzip.decompress(base64.b64decode(payload)).decode("utf-8")
        self.assertIn("JarvisPcAudio", script)
        self.assertIn("$before + (-10)", script)
        self.assertLess(len(" ".join(command)), 8000)

    def test_invalid_action_is_rejected(self):
        with self.assertRaises(ValueError):
            build_windows_audio_command("run-anything", 10)

    def test_windows_output_is_strictly_parsed(self):
        self.assertEqual(
            parse_windows_audio_output("BEFORE=45\nVOLUME=50\nMUTED=False\n"),
            {"before": 45, "volume": 50, "muted": False},
        )
        with self.assertRaises(ValueError):
            parse_windows_audio_output("unexpected output")

    def test_remote_control_uses_non_interactive_ssh(self):
        result = Mock(
            returncode=0,
            stdout="BEFORE=45\nVOLUME=50\nMUTED=False\n",
            stderr="",
        )
        with patch.object(jarvis.subprocess, "run", return_value=result) as run:
            state = jarvis.control_remote_pc_volume(
                "change", 5, user="USER", host="192.168.2.13"
            )
        self.assertEqual(state["volume"], 50)
        args = run.call_args.args[0]
        self.assertEqual(args[:7], [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ConnectionAttempts=1",
        ])
        self.assertEqual(args[7], "USER@192.168.2.13")
        self.assertEqual(args[8], "powershell.exe")
        self.assertNotIn("shutdown", " ".join(args).lower())
        self.assertNotIn("input", run.call_args.kwargs)


class WindowsAudioRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pc_increase_routes_to_windows_without_touching_ubuntu(self):
        state = {"before": 40, "volume": 50, "muted": False}
        with patch.object(
            jarvis.asyncio, "to_thread", new=AsyncMock(return_value=state)
        ) as to_thread, patch.object(jarvis, "change_volume") as ubuntu:
            handled = await jarvis.route_command(
                "tăng âm lượng PC 10", allow_local_ai=False
            )
        self.assertTrue(handled)
        to_thread.assert_awaited_once_with(
            jarvis.control_remote_pc_volume, "change", 10
        )
        ubuntu.assert_not_called()
        self.assertIn("Đã tăng âm lượng PC: 40% → 50%", jarvis.last_command_response)

    async def test_command_without_pc_keeps_existing_ubuntu_route(self):
        with patch.object(
            jarvis, "change_volume", return_value=(40, 45)
        ) as ubuntu, patch.object(jarvis, "control_remote_pc_volume") as remote:
            handled = await jarvis.route_command(
                "tăng âm lượng", allow_local_ai=False
            )
        self.assertTrue(handled)
        ubuntu.assert_called_once_with(5)
        remote.assert_not_called()
        self.assertIn("Đã tăng âm lượng: 40% → 45%", jarvis.last_command_response)


if __name__ == "__main__":
    unittest.main()
