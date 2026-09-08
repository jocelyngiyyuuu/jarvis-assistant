import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from jarvis_core.desktop_control import (
    DesktopController,
    parse_monitor_role,
    parse_xrandr_monitors,
    strip_monitor_suffix,
)
import jarvis


XRANDR = """\
DP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis)
HDMI-1 connected 1920x1080+1920+0 (normal left inverted right x axis y axis)
"""


class DesktopControlTests(unittest.TestCase):
    def test_parses_monitor_layout_and_roles(self):
        monitors = parse_xrandr_monitors(XRANDR)
        self.assertEqual([(m.name, m.x, m.primary) for m in monitors], [
            ("DP-1", 0, True), ("HDMI-1", 1920, False),
        ])
        self.assertEqual(parse_monitor_role("trên màn hình chính"), "primary")
        self.assertEqual(parse_monitor_role("sang màn hình phụ"), "secondary")
        self.assertEqual(parse_monitor_role("màn hình 2"), "index:2")

    def test_strips_only_monitor_suffix(self):
        self.assertEqual(
            strip_monitor_suffix("mở zalo trên màn hình phụ"), "mở zalo"
        )

    def test_preferences_are_persistent_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = DesktopController(Path(directory))
            controller.set_preference("zalo", "secondary")
            self.assertEqual(controller.preference("zalo"), "secondary")
            self.assertEqual(controller.preferences_path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(controller.clear_preference("zalo"))
            self.assertIsNone(controller.preference("zalo"))

    def test_moves_verified_window_to_secondary_monitor(self):
        controller = DesktopController("/tmp/jarvis-desktop-test")
        completed = MagicMock(returncode=0)
        with patch.object(controller, "monitors", return_value=parse_xrandr_monitors(XRANDR)), patch(
            "jarvis_core.desktop_control.subprocess.run", return_value=completed
        ) as run:
            ok, _ = controller.move_window("0x42", "secondary")
        self.assertTrue(ok)
        geometry = [call.args[0] for call in run.call_args_list if "-e" in call.args[0]][0]
        self.assertIn("0,1920,0,-1,-1", geometry)

    def test_rejects_unverified_window_id(self):
        controller = DesktopController("/tmp/jarvis-desktop-test")
        ok, _ = controller.move_window("not-a-window", "primary")
        self.assertFalse(ok)

    def test_capture_command_sets_private_attachment(self):
        screenshot = Path("/tmp/screen-test.png")
        with patch.object(jarvis.DESKTOP, "capture_screen", return_value=screenshot):
            self.assertTrue(jarvis.handle_desktop_command("chụp màn hình"))
        self.assertEqual(jarvis.last_response_attachment, str(screenshot))
        self.assertEqual(jarvis.last_spoken_response, "Đã chụp màn hình.")

    def test_capture_for_red_marker_explains_discord_workflow(self):
        screenshot = Path("/tmp/screen-marker-test.png")
        with patch.object(jarvis.DESKTOP, "capture_screen", return_value=screenshot):
            self.assertTrue(jarvis.handle_desktop_command(
                "chụp màn hình để chọn"
            ))
        self.assertEqual(jarvis.last_response_attachment, str(screenshot))
        self.assertIn("dấu chấm", jarvis.last_command_response)
        self.assertEqual(
            jarvis.last_spoken_response, "Đã gửi ảnh để bạn đánh dấu."
        )

    def test_explicit_zalo_monitor_open_moves_verified_window(self):
        with patch("jarvis.handle_chrome_shortcut", return_value=True) as opened, patch(
            "jarvis._move_opened_automation_app",
            return_value=(True, "Đã chuyển cửa sổ sang màn hình phụ."),
        ) as moved:
            jarvis.set_command_response("✅ Đã mở Zalo.")
            self.assertTrue(jarvis.handle_automation_open_on_monitor(
                "mở zalo trên màn hình phụ"
            ))
        opened.assert_called_once_with("mở zalo")
        moved.assert_called_once_with("zalo", "secondary")
        self.assertIn("Đã chuyển", jarvis.last_command_response)


class SemanticDesktopCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _direct_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def test_qwen_selected_control_is_activated(self):
        controls = [{
            "id": "send-id", "name": "Gửi", "role": "button", "app": "Zalo",
            "bounds": [100, 200, 40, 20],
        }]
        with patch("jarvis.asyncio.to_thread", new=AsyncMock(
            side_effect=self._direct_to_thread
        )), patch("jarvis._semantic_browser_controls", new=AsyncMock(
            return_value=None
        )), patch.object(
            jarvis.SEMANTIC_DESKTOP, "controls", return_value=controls
        ), patch.object(
            jarvis.CORE.local_ai, "choose_accessible_target", return_value=controls[0]
        ) as choose, patch.object(
            jarvis.SEMANTIC_DESKTOP, "activate", return_value=(True, {})
        ) as activate, patch.object(
            jarvis.DESKTOP, "move_pointer", return_value=True
        ), patch("jarvis._active_xwayland_window_title", return_value="Zalo"):
            handled = await jarvis.handle_semantic_click_command("nhấn nút Gửi")
        self.assertTrue(handled)
        choose.assert_called_once()
        activate.assert_called_once_with("send-id", "Zalo")
        self.assertEqual(
            jarvis.last_command_response,
            "✅ Đã di chuột và nhấn “Gửi” trong Zalo.",
        )

    async def test_ambiguous_qwen_result_never_clicks(self):
        controls = [{"id": "one", "name": "Tiếp tục", "role": "button"}]
        with patch("jarvis.asyncio.to_thread", new=AsyncMock(
            side_effect=self._direct_to_thread
        )), patch("jarvis._semantic_browser_controls", new=AsyncMock(
            return_value=None
        )), patch.object(
            jarvis.SEMANTIC_DESKTOP, "controls", return_value=controls
        ), patch.object(
            jarvis.CORE.local_ai, "choose_accessible_target", return_value=None
        ), patch.object(jarvis.SEMANTIC_DESKTOP, "activate") as activate:
            handled = await jarvis.handle_semantic_click_command("nhấn nút tiếp tục")
        self.assertTrue(handled)
        activate.assert_not_called()
        self.assertIn("chưa xác định chắc chắn", jarvis.last_command_response)

    async def test_qwen_selected_web_control_uses_cdp_activation(self):
        control = {
            "id": "token-2", "name": "Gửi", "role": "button", "app": "zalo",
            "screenBounds": [100, 200, 40, 20],
        }
        context = {"controls": [control], "token": "token", "page_id": 7}
        with patch("jarvis.asyncio.to_thread", new=AsyncMock(
            side_effect=self._direct_to_thread
        )), patch("jarvis._semantic_browser_controls", new=AsyncMock(
            return_value=context
        )), patch.object(
            jarvis.CORE.local_ai, "choose_accessible_target", return_value=control
        ), patch("jarvis._activate_semantic_browser_control", new=AsyncMock(
            return_value=True
        )) as activate, patch.object(
            jarvis.DESKTOP, "move_pointer", return_value=True
        ):
            handled = await jarvis.handle_semantic_click_command("bấm nút Gửi")
        self.assertTrue(handled)
        activate.assert_awaited_once_with(context, "token-2")
        self.assertIn("Đã di chuột và nhấn", jarvis.last_command_response)

    def test_semantic_label_requires_full_short_phrase(self):
        self.assertTrue(jarvis._semantic_label_matches("thư rác", "Thư rác"))
        self.assertTrue(jarvis._semantic_label_matches(
            "hiện thêm", "Hiển thị thêm"
        ))
        self.assertFalse(jarvis._semantic_label_matches(
            "thư rác", "Tìm kiếm trong thư"
        ))
        self.assertFalse(jarvis._semantic_label_matches(
            "hiện thêm", "Thêm nhãn"
        ))

    async def test_unrelated_partial_match_is_never_sent_to_qwen_or_clicked(self):
        controls = [{
            "id": "search", "name": "Tìm kiếm trong thư",
            "role": "entry", "app": "gmail",
        }]
        context = {"controls": controls, "token": "token", "page_id": 7}
        with patch("jarvis._semantic_browser_controls", new=AsyncMock(
            return_value=context
        )), patch.object(
            jarvis.CORE.local_ai, "choose_accessible_target"
        ) as choose, patch(
            "jarvis._activate_semantic_browser_control", new=AsyncMock()
        ) as activate, patch(
            "jarvis._handle_visual_click", new=AsyncMock(return_value=True)
        ) as visual:
            handled = await jarvis.handle_semantic_click_command("nhấn vào thư rác")
        self.assertTrue(handled)
        choose.assert_not_called()
        activate.assert_not_awaited()
        visual.assert_awaited_once_with("thu rac")

    async def test_visual_fallback_captures_screen_and_clicks_confident_target(self):
        screenshot = Path("/tmp/qwen-vl-screen.png")
        target = {
            "x": 400, "y": 300, "confidence": 0.97,
            "description": "biểu tượng cài đặt",
        }
        with patch("jarvis.asyncio.to_thread", new=AsyncMock(
            side_effect=self._direct_to_thread
        )), patch.object(
            jarvis.DESKTOP, "capture_screen", return_value=screenshot
        ), patch.object(
            jarvis.CORE.local_ai, "choose_visual_target", return_value=target
        ) as choose, patch.object(
            jarvis.DESKTOP, "click", return_value=True
        ) as click:
            handled = await jarvis._handle_visual_click("bieu tuong cai dat")
        self.assertTrue(handled)
        choose.assert_called_once_with("bieu tuong cai dat", screenshot)
        click.assert_called_once_with(400, 300)
        self.assertIn("Qwen VL đã nhìn màn hình", jarvis.last_command_response)
        self.assertNotIn("biểu tượng cài đặt", jarvis.last_command_response)


if __name__ == "__main__":
    unittest.main()
