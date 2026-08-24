import unittest
from unittest.mock import AsyncMock, patch

import jarvis
from jarvis_core.voice_trigger import (
    ImpulsePatternDetector,
    pcm_metrics,
    run_voice_trigger_loop,
)


class ImpulsePatternDetectorTests(unittest.TestCase):
    def test_pcm_metrics_for_silence(self):
        self.assertEqual(pcm_metrics(b"\x00\x00" * 320), (0.0, 0, 0.0))

    def test_one_short_high_frequency_impulse_is_snap(self):
        detector = ImpulsePatternDetector(calibration_frames=0)
        self.assertIsNone(detector.feed_metrics(3_200, 12_000, 0.24, now=1.00))
        self.assertIsNone(detector.feed_metrics(80, 200, 0.02, now=1.02))
        self.assertIsNone(detector.feed_metrics(70, 180, 0.01, now=1.04))
        self.assertEqual(detector.feed_metrics(70, 180, 0.01, now=1.90), "snap")

    def test_two_snap_shaped_claps_are_not_mistaken_for_one_snap(self):
        detector = ImpulsePatternDetector(calibration_frames=0)
        result = None
        for base in (1.0, 1.35):
            detector.feed_metrics(3_200, 12_000, 0.24, now=base)
            detector.feed_metrics(80, 200, 0.02, now=base + 0.02)
            result = detector.feed_metrics(70, 180, 0.01, now=base + 0.04)
        self.assertEqual(result, "double_clap")

    def test_screen_wake_sensitivity_accepts_measured_clap_impulses(self):
        detector = ImpulsePatternDetector(
            calibration_frames=0,
            onset_rms_floor=800,
            onset_peak_floor=3_000,
            noise_rms_multiplier=3.5,
            noise_peak_multiplier=9.0,
            max_clap_interval=2.5,
            defer_snap_for_double=False,
        )
        for base in (1.0, 3.0):
            detector.feed_metrics(1_800, 5_500, 0.08, now=base)
            detector.feed_metrics(80, 180, 0.01, now=base + 0.02)
            result = detector.feed_metrics(70, 160, 0.01, now=base + 0.04)
        self.assertEqual(result, "double_clap")

    def test_screen_wake_sensitivity_ignores_small_air_conditioner_noise(self):
        detector = ImpulsePatternDetector(
            calibration_frames=0,
            onset_rms_floor=800,
            onset_peak_floor=3_000,
            noise_rms_multiplier=3.5,
            noise_peak_multiplier=9.0,
            max_clap_interval=2.5,
            defer_snap_for_double=False,
        )
        for frame in range(200):
            self.assertIsNone(
                detector.feed_metrics(
                    650, 2_500, 0.06, now=1.0 + frame * 0.02
                )
            )

    def test_screen_wake_mode_accepts_one_sharp_snap_immediately(self):
        detector = ImpulsePatternDetector(
            calibration_frames=0,
            defer_snap_for_double=False,
        )
        detector.feed_metrics(3_200, 12_000, 0.24, now=1.00)
        detector.feed_metrics(80, 200, 0.02, now=1.02)
        self.assertEqual(
            detector.feed_metrics(70, 180, 0.01, now=1.04), "snap"
        )

    def test_two_clap_impulses_trigger_with_expected_interval(self):
        detector = ImpulsePatternDetector(calibration_frames=0)
        for now in (1.00, 1.02, 1.04):
            result = detector.feed_metrics(
                4_000 if now == 1.00 else 100,
                8_000 if now == 1.00 else 200,
                0.05,
                now=now,
            )
        self.assertIsNone(result)
        for now in (1.35, 1.37, 1.39):
            result = detector.feed_metrics(
                4_200 if now == 1.35 else 100,
                8_200 if now == 1.35 else 200,
                0.06,
                now=now,
            )
        self.assertEqual(result, "double_clap")

    def test_suppression_drops_partial_pattern(self):
        detector = ImpulsePatternDetector(calibration_frames=0)
        detector.feed_metrics(4_000, 8_000, 0.05, now=1.00)
        detector.feed_metrics(100, 200, 0.01, now=1.02)
        detector.feed_metrics(100, 200, 0.01, now=1.04)
        self.assertIsNone(
            detector.feed_metrics(4_000, 8_000, 0.05, now=1.30, suppressed=True)
        )
        self.assertEqual(list(detector.clap_times), [])

    def test_snap_shape_counts_as_clap_when_only_cancel_pair_is_allowed(self):
        detector = ImpulsePatternDetector(
            calibration_frames=0, recognize_snap=False
        )
        for base in (1.0, 1.35):
            detector.feed_metrics(3_200, 12_000, 0.24, now=base)
            detector.feed_metrics(80, 200, 0.02, now=base + 0.02)
            result = detector.feed_metrics(70, 180, 0.01, now=base + 0.04)
        self.assertEqual(result, "double_clap")


class VoiceCommandSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        jarvis.last_command_response = None

    async def test_dangerous_voice_command_requires_another_interface(self):
        with patch.object(jarvis, "route_command", new=AsyncMock()) as route, \
             patch.object(jarvis, "speak") as speak:
            self.assertFalse(await jarvis.handle_voice_command("sleep sâu", "snap"))
        route.assert_not_awaited()
        speak.assert_called_once()
        self.assertIn("cần xác nhận", jarvis.last_command_response)

    async def test_normal_voice_command_uses_existing_router(self):
        async def route(command, **_kwargs):
            jarvis.set_command_response(f"Đã chạy: {command}")
            return True

        with patch.object(jarvis, "route_command", side_effect=route) as routed, \
             patch.object(jarvis, "speak_last_response") as speak:
            self.assertTrue(await jarvis.handle_voice_command("mở youtube", "double_clap"))
        routed.assert_awaited_once_with("mở youtube", source="voice")
        speak.assert_called_once_with()

    async def test_sensor_status_is_available_through_normal_router(self):
        engine = unittest.mock.Mock()
        engine.available.return_value = True
        engine.stop_event.is_set.return_value = False
        engine.is_paused.return_value = False
        with patch.object(jarvis, "voice_trigger_engine", engine):
            self.assertTrue(await jarvis.route_command(
                "trạng thái cảm biến âm thanh", allow_local_ai=False
            ))
        self.assertIn("đang bật", jarvis.last_command_response)

    async def test_voice_sensor_can_be_paused_and_resumed(self):
        engine = unittest.mock.Mock()
        with patch.object(jarvis, "voice_trigger_engine", engine):
            self.assertTrue(await jarvis.route_command(
                "tắt giọng nói tạm thời", allow_local_ai=False
            ))
            engine.pause.assert_called_once_with()
            self.assertIn("tạm tắt", jarvis.last_command_response)

            self.assertTrue(await jarvis.route_command(
                "bật giọng nói", allow_local_ai=False
            ))
            engine.resume.assert_called_once_with()
            self.assertIn("bật lại", jarvis.last_command_response)

    async def test_clap_screen_wake_can_be_disabled_and_enabled(self):
        engine = unittest.mock.Mock()
        with patch.object(jarvis, "voice_trigger_engine", engine), \
             patch.object(jarvis, "save_clap_screen_wake_enabled") as save:
            self.assertTrue(await jarvis.route_command(
                "tắt vỗ tay", allow_local_ai=False
            ))
            self.assertFalse(jarvis.CLAP_SCREEN_WAKE_ENABLED)
            engine.disarm_screen_wake.assert_called_once_with()
            save.assert_called_with(False)

            self.assertTrue(await jarvis.route_command(
                "bật vỗ tay", allow_local_ai=False
            ))
            self.assertTrue(jarvis.CLAP_SCREEN_WAKE_ENABLED)
            save.assert_called_with(True)

    def test_pausing_engine_releases_microphone_process(self):
        from jarvis_core.voice_trigger import VoiceTriggerEngine

        engine = VoiceTriggerEngine("/tmp/missing-model")
        with patch.object(engine, "_stop_capture") as stop_capture:
            engine.pause()
        self.assertTrue(engine.is_paused())
        stop_capture.assert_called_once_with()
        engine.resume()
        self.assertFalse(engine.is_paused())

    def test_screen_wake_mode_temporarily_allows_capture_while_paused(self):
        from jarvis_core.voice_trigger import VoiceTriggerEngine

        engine = VoiceTriggerEngine("/tmp/missing-model")
        with patch.object(engine, "_stop_capture") as stop_capture:
            engine.pause()
            self.assertFalse(engine._capture_allowed())
            engine.arm_screen_wake()
            self.assertTrue(engine.screen_wake_armed())
            self.assertTrue(engine._capture_allowed())
            engine.disarm_screen_wake()
        self.assertFalse(engine._capture_allowed())
        self.assertEqual(stop_capture.call_count, 2)

    def test_buffered_pcm_uses_audio_timeline_for_double_clap(self):
        from jarvis_core.voice_trigger import FRAME_BYTES, VoiceTriggerEngine

        metrics = []
        for frame in range(24):
            if frame in {0, 20}:
                metrics.append((1_500, 5_000, 0.08))
            else:
                metrics.append((80, 180, 0.01))

        class Output:
            def read(self, _size):
                return b"\0" * FRAME_BYTES

        process = unittest.mock.Mock(stdout=Output())
        engine = VoiceTriggerEngine("/tmp/missing-model")
        engine.pause()
        engine.arm_screen_wake()
        with patch.object(engine, "_start_capture", return_value=process), \
             patch.object(engine, "_stop_capture"), \
             patch(
                 "jarvis_core.voice_trigger.pcm_metrics",
                 side_effect=metrics,
             ):
            self.assertEqual(engine.wait_for_trigger(), "double_clap")

    async def test_armed_audio_gesture_wakes_screen_without_listening(self):
        engine = unittest.mock.Mock()
        engine.screen_wake_armed.return_value = True
        with patch.object(jarvis, "CLAP_SCREEN_WAKE_ENABLED", True), \
             patch.object(jarvis, "voice_trigger_engine", engine), \
             patch.object(jarvis, "wake_screen", return_value=True) as wake, \
             patch.object(jarvis, "speak") as speak:
            handled = await jarvis.handle_screen_wake_trigger("double_clap")
        self.assertTrue(handled)
        wake.assert_called_once_with()
        engine.disarm_screen_wake.assert_called_once_with()
        speak.assert_called_once_with("Jarvis đã bật màn hình.")

    async def test_disabled_clap_wake_does_not_consume_audio_gesture(self):
        engine = unittest.mock.Mock()
        engine.screen_wake_armed.return_value = True
        with patch.object(jarvis, "CLAP_SCREEN_WAKE_ENABLED", False), \
             patch.object(jarvis, "voice_trigger_engine", engine), \
             patch.object(jarvis, "wake_screen") as wake:
            handled = await jarvis.handle_screen_wake_trigger("double_clap")
        self.assertFalse(handled)
        wake.assert_not_called()

    def test_screen_sleep_arms_audio_wake_without_changing_voice_pause(self):
        engine = unittest.mock.Mock()
        result = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        with patch.dict(jarvis.os.environ, {"XDG_SESSION_TYPE": "wayland"}), \
             patch.object(jarvis, "CLAP_SCREEN_WAKE_ENABLED", True), \
             patch.object(jarvis, "voice_trigger_engine", engine), \
             patch.object(jarvis.subprocess, "run", return_value=result):
            self.assertTrue(jarvis.sleep_screen_only())
        engine.arm_screen_wake.assert_called_once_with()
        engine.resume.assert_not_called()

    def test_screen_sleep_does_not_arm_audio_wake_when_disabled(self):
        engine = unittest.mock.Mock()
        result = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        with patch.dict(jarvis.os.environ, {"XDG_SESSION_TYPE": "wayland"}), \
             patch.object(jarvis, "CLAP_SCREEN_WAKE_ENABLED", False), \
             patch.object(jarvis, "voice_trigger_engine", engine), \
             patch.object(jarvis.subprocess, "run", return_value=result):
            self.assertTrue(jarvis.sleep_screen_only())
        engine.arm_screen_wake.assert_not_called()
        engine.disarm_screen_wake.assert_called_once_with()

    def test_normal_screen_wake_disarms_temporary_audio_wake(self):
        engine = unittest.mock.Mock()
        result = unittest.mock.Mock(returncode=0)
        with patch.dict(jarvis.os.environ, {"XDG_SESSION_TYPE": "wayland"}), \
             patch.object(jarvis, "voice_trigger_engine", engine), \
             patch.object(jarvis.subprocess, "run", return_value=result):
            self.assertTrue(jarvis.wake_screen())
        engine.disarm_screen_wake.assert_called_once_with()

    def test_youtube_pronunciation_aliases_are_normalized(self):
        for command in ("mở du túp", "mở you tube", "mo iu tup"):
            with self.subTest(command=command):
                self.assertEqual(jarvis.normalize_voice_command(command), "mở youtube")

    async def test_ready_announcement_uses_jarvis_tts(self):
        with patch.object(jarvis, "speak") as speak, patch.object(
            jarvis.asyncio, "sleep", new=AsyncMock()
        ) as sleep:
            await jarvis.announce_voice_ready("double_clap")
        speak.assert_called_once_with("Jarvis đang nghe.")
        sleep.assert_awaited_once_with(0.1)

    async def test_second_double_clap_cancels_listening_session(self):
        class StopEvent:
            def is_set(self):
                return False

        class Engine:
            def __init__(self):
                self.stop_event = StopEvent()
                self.last_capture_cancelled = False
                self.calls = 0

            def available(self):
                return True

            def wait_for_trigger(self):
                self.calls += 1
                return "double_clap" if self.calls == 1 else None

            def capture_command(self):
                self.last_capture_cancelled = True
                return b""

            def transcribe(self, _pcm):
                raise AssertionError("cancelled audio must not be transcribed")

        ready = AsyncMock()
        cancelled = AsyncMock()
        command = AsyncMock()
        async def inline_thread(function, *args):
            return function(*args)

        with patch(
            "jarvis_core.voice_trigger.asyncio.sleep", new=AsyncMock()
        ), patch(
            "jarvis_core.voice_trigger.asyncio.to_thread",
            side_effect=inline_thread,
        ):
            await run_voice_trigger_loop(
                Engine(), command,
                ready_callback=ready,
                cancelled_callback=cancelled,
            )
        ready.assert_awaited_once_with("double_clap")
        cancelled.assert_awaited_once_with()
        command.assert_not_awaited()

    async def test_empty_capture_announces_that_speech_was_not_recognized(self):
        class StopEvent:
            def is_set(self):
                return False

        class Engine:
            def __init__(self):
                self.stop_event = StopEvent()
                self.last_capture_cancelled = False
                self.calls = 0

            def available(self):
                return True

            def wait_for_trigger(self):
                self.calls += 1
                return "snap" if self.calls == 1 else None

            def capture_command(self):
                return b""

            def transcribe(self, _pcm):
                return ""

        unrecognized = AsyncMock()
        async def inline_thread(function, *args):
            return function(*args)

        with patch(
            "jarvis_core.voice_trigger.asyncio.sleep", new=AsyncMock()
        ), patch(
            "jarvis_core.voice_trigger.asyncio.to_thread",
            side_effect=inline_thread,
        ):
            await run_voice_trigger_loop(
                Engine(), AsyncMock(), unrecognized_callback=unrecognized
            )
        unrecognized.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
