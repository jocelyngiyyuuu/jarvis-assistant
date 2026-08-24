import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import jarvis

TTS_DIR = Path(__file__).resolve().parents[1] / "tts"
if str(TTS_DIR) not in sys.path:
    sys.path.insert(0, str(TTS_DIR))
import tts_playback
import tts_limits


class JarvisTtsRoutingTests(unittest.TestCase):
    def test_response_can_explicitly_disable_tts(self):
        jarvis.set_command_response("Đã sleep màn hình.", spoken_message=False)
        with patch.object(jarvis, "speak") as speak:
            jarvis.speak_last_response()
        speak.assert_not_called()

    def test_gtk_ipc_speaks_last_response(self):
        async def run():
            reader = AsyncMock()
            reader.readline = AsyncMock(
                side_effect=[
                    (
                        json.dumps(
                            {"command": "help", "source": "gtk"}
                        ).encode("utf-8")
                        + b"\n"
                    ),
                    b"",
                ]
            )
            writer = Mock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()

            async def fake_route(command, source="terminal"):
                jarvis.set_command_response("Sẵn sàng.")
                return True

            with patch.object(jarvis.CORE.conversation, "add"), \
                 patch.object(
                     jarvis, "resolve_command_confirmation",
                     return_value=("help", None),
                 ), \
                 patch.object(
                     jarvis, "route_command", AsyncMock(side_effect=fake_route)
                 ) as routed, \
                 patch.object(jarvis, "speak_last_response") as spoken:
                await jarvis._handle_ipc_client(reader, writer)

            routed.assert_awaited_once_with("help", source="gtk")
            spoken.assert_called_once()
            payload = json.loads(writer.write.call_args.args[0].decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["response"], "Sẵn sàng.")

        asyncio.run(run())

    def test_silent_gtk_status_request_does_not_speak_or_fill_chat(self):
        async def run():
            reader = AsyncMock()
            reader.readline = AsyncMock(
                side_effect=[
                    json.dumps({
                        "command": "trạng thái giọng nói",
                        "source": "gtk",
                        "silent": True,
                    }, ensure_ascii=False).encode("utf-8") + b"\n",
                    b"",
                ]
            )
            writer = Mock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()

            async def fake_route(command, source="terminal"):
                jarvis.set_command_response("🔇 Cảm biến đang tạm tắt.")
                return True

            with patch.object(jarvis.CORE.conversation, "add") as add, \
                 patch.object(
                     jarvis, "resolve_command_confirmation",
                     return_value=("trạng thái giọng nói", None),
                 ), \
                 patch.object(
                     jarvis, "route_command", AsyncMock(side_effect=fake_route)
                 ), \
                 patch.object(jarvis, "speak_last_response") as spoken:
                await jarvis._handle_ipc_client(reader, writer)

            add.assert_not_called()
            spoken.assert_not_called()
            payload = json.loads(writer.write.call_args.args[0].decode("utf-8"))
            self.assertTrue(payload["ok"])

        asyncio.run(run())


class TtsPlaybackTests(unittest.TestCase):
    def test_play_wav_prefers_paplay_on_pipewire(self):
        wav = Path("/tmp/jarvis-test.wav")
        with patch.object(tts_playback.shutil, "which", side_effect=lambda name: name == "paplay"), \
             patch.object(
                 tts_playback.subprocess, "run",
                 return_value=Mock(returncode=0, stdout="", stderr=""),
             ) as run:
            self.assertTrue(tts_playback.play_wav(wav))
        run.assert_called_once_with(
            ["paplay", str(wav)], capture_output=True, text=True
        )

    def test_play_wav_falls_back_to_aplay(self):
        wav = Path("/tmp/jarvis-test.wav")

        def which(name):
            return name if name == "aplay" else None

        with patch.object(tts_playback.shutil, "which", side_effect=which), \
             patch.object(
                 tts_playback.subprocess, "run",
                 return_value=Mock(returncode=0, stdout="", stderr=""),
             ) as run:
            self.assertTrue(tts_playback.play_wav(wav))
        run.assert_called_once_with(
            ["aplay", "-q", str(wav)], capture_output=True, text=True
        )


class TtsLimitTests(unittest.TestCase):
    def test_short_zalo_reply_stays_well_under_default_24s_cap(self):
        frames = tts_limits.max_new_frames_for_text("Đã mở Zalo.")
        self.assertLessEqual(frames, 50)
        self.assertGreaterEqual(frames, tts_limits.MIN_FRAMES)
        self.assertLess(frames / tts_limits.FRAME_HZ, 5)

    def test_long_reply_still_has_an_upper_bound(self):
        frames = tts_limits.max_new_frames_for_text("a" * 400)
        self.assertEqual(frames, tts_limits.MAX_FRAMES)
