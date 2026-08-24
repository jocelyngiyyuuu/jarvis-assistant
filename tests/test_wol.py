import unittest
from unittest.mock import Mock, patch

import jarvis


class WakeOnLanTests(unittest.IsolatedAsyncioTestCase):
    def test_magic_packet_has_standard_layout(self):
        packet = jarvis.build_wol_magic_packet("34:5A:60:F4:61:90")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("345A60F46190"))
        self.assertEqual(packet[-6:], bytes.fromhex("345A60F46190"))

    def test_invalid_mac_is_rejected(self):
        with self.assertRaises(ValueError):
            jarvis.build_wol_magic_packet("not-a-mac")

    def test_sender_broadcasts_three_packets(self):
        client = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        with patch.object(jarvis.socket, "socket", return_value=context):
            count = jarvis.send_wake_on_lan(
                "34:5A:60:F4:61:90",
                broadcast="192.168.2.255",
                port=9,
            )
        self.assertEqual(count, 3)
        client.setsockopt.assert_called_once_with(
            jarvis.socket.SOL_SOCKET, jarvis.socket.SO_BROADCAST, 1
        )
        self.assertEqual(client.sendto.call_count, 3)
        self.assertEqual(
            client.sendto.call_args.args[1], ("192.168.2.255", 9)
        )

    async def test_bat_pc_routes_to_wol_sender(self):
        for command in ("bật PC", "bật máy tính", "đánh thức PC"):
            with self.subTest(command=command), patch.object(
                jarvis, "send_wake_on_lan", return_value=3
            ) as send:
                handled = await jarvis.route_command(
                    command, allow_local_ai=False
                )
                self.assertTrue(handled)
                send.assert_called_once_with()
                self.assertIn("Đã bật PC", jarvis.last_command_response)

    def test_shutdown_pc_uses_non_interactive_ssh(self):
        result = Mock(returncode=0)
        with patch.object(jarvis.subprocess, "run", return_value=result) as run:
            self.assertTrue(jarvis.shutdown_remote_pc("USER", "192.168.2.13"))
        run.assert_called_once_with(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "-o", "ConnectionAttempts=1",
                "USER@192.168.2.13",
                "shutdown /s /t 0",
            ],
            stdout=jarvis.subprocess.DEVNULL,
            stderr=jarvis.subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )

    async def test_tat_pc_routes_to_ssh_shutdown(self):
        with patch.object(jarvis, "shutdown_remote_pc", return_value=True) as shutdown:
            handled = await jarvis.route_command(
                "tắt PC", allow_local_ai=False
            )
        self.assertTrue(handled)
        shutdown.assert_called_once_with()
        self.assertIn("Đã tắt PC", jarvis.last_command_response)


if __name__ == "__main__":
    unittest.main()
