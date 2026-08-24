"""Local clap/snap wake trigger and Vietnamese speech recognition."""

from array import array
import asyncio
from collections import deque
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time


SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


def pcm_metrics(data):
    """Return RMS, absolute peak and zero-crossing ratio for signed 16-bit PCM."""
    samples = array("h")
    samples.frombytes(data[:len(data) - len(data) % 2])
    if not samples:
        return 0.0, 0, 0.0
    if os.sys.byteorder != "little":
        samples.byteswap()
    squares = sum(int(value) * int(value) for value in samples)
    rms = math.sqrt(squares / len(samples))
    peak = max(abs(int(value)) for value in samples)
    crossings = sum(
        1 for previous, current in zip(samples, samples[1:])
        if (previous < 0 <= current) or (previous >= 0 > current)
    )
    zcr = crossings / max(1, len(samples) - 1)
    return rms, peak, zcr


class ImpulsePatternDetector:
    """Detect one high-frequency snap or two clap-like impulses."""

    def __init__(
        self,
        *,
        calibration_frames=50,
        recognize_snap=True,
        onset_rms_floor=900.0,
        onset_peak_floor=4_500.0,
        noise_rms_multiplier=4.5,
        noise_peak_multiplier=12.0,
        max_clap_interval=0.85,
        defer_snap_for_double=True,
    ):
        self.noise_rms = 220.0
        self.calibration_frames = max(0, int(calibration_frames))
        self.calibration_values = []
        self.recognize_snap = bool(recognize_snap)
        self.onset_rms_floor = float(onset_rms_floor)
        self.onset_peak_floor = float(onset_peak_floor)
        self.noise_rms_multiplier = float(noise_rms_multiplier)
        self.noise_peak_multiplier = float(noise_peak_multiplier)
        self.max_clap_interval = max(0.12, float(max_clap_interval))
        self.defer_snap_for_double = bool(defer_snap_for_double)
        self.active = False
        self.event_started = 0.0
        self.event_frames = 0
        self.quiet_frames = 0
        self.max_rms = 0.0
        self.max_peak = 0
        self.max_zcr = 0.0
        self.clap_times = deque(maxlen=2)
        self.pending_snap_at = None
        self.cooldown_until = 0.0

    def reset_event(self):
        self.active = False
        self.event_started = 0.0
        self.event_frames = 0
        self.quiet_frames = 0
        self.max_rms = 0.0
        self.max_peak = 0
        self.max_zcr = 0.0

    def _finish_event(self, now):
        duration = self.event_frames * FRAME_MS / 1000
        crest = self.max_peak / max(1.0, self.max_rms)
        is_snap = (
            duration <= 0.10
            and self.max_zcr >= 0.16
            and crest >= 2.2
            and self.max_peak >= 7_000
        )
        self.reset_event()
        if is_snap and self.recognize_snap:
            if not self.defer_snap_for_double:
                self.pending_snap_at = None
                self.clap_times.clear()
                self.cooldown_until = now + 1.2
                return "snap"
            # A close clap can have the same short, high-frequency shape as a
            # finger snap. Keep the first impulse briefly so a second one can
            # turn the gesture into a deliberate double clap.
            previous = (
                self.pending_snap_at
                if self.pending_snap_at is not None
                else (self.clap_times[-1] if self.clap_times else None)
            )
            if (
                previous is not None
                and 0.12 <= now - previous <= self.max_clap_interval
            ):
                self.pending_snap_at = None
                self.clap_times.clear()
                self.cooldown_until = now + 1.2
                return "double_clap"
            self.pending_snap_at = now
            self.clap_times.clear()
            return None

        if self.pending_snap_at is not None:
            self.clap_times.append(self.pending_snap_at)
            self.pending_snap_at = None

        self.clap_times.append(now)
        if len(self.clap_times) == 2:
            interval = self.clap_times[-1] - self.clap_times[-2]
            if 0.12 <= interval <= self.max_clap_interval:
                self.clap_times.clear()
                self.cooldown_until = now + 1.2
                return "double_clap"
        return None

    def feed_metrics(self, rms, peak, zcr, now=None, *, suppressed=False):
        now = time.monotonic() if now is None else float(now)
        if suppressed or now < self.cooldown_until:
            self.clap_times.clear()
            self.pending_snap_at = None
            self.reset_event()
            return None

        if len(self.calibration_values) < self.calibration_frames:
            self.calibration_values.append(float(rms))
            if len(self.calibration_values) == self.calibration_frames:
                ordered = sorted(self.calibration_values)
                # A percentile above the median tolerates fan noise while a
                # single accidental bump during startup cannot dominate.
                index = min(len(ordered) - 1, int(len(ordered) * 0.65))
                self.noise_rms = max(120.0, ordered[index])
            return None

        onset_rms = max(
            self.onset_rms_floor,
            self.noise_rms * self.noise_rms_multiplier,
        )
        onset_peak = max(
            self.onset_peak_floor,
            self.noise_rms * self.noise_peak_multiplier,
        )
        loud = rms >= onset_rms and peak >= onset_peak

        if not self.active:
            if (
                self.recognize_snap
                and self.pending_snap_at is not None
                and now - self.pending_snap_at > self.max_clap_interval
            ):
                self.pending_snap_at = None
                self.clap_times.clear()
                self.cooldown_until = now + 1.2
                return "snap"
            if loud:
                self.active = True
                self.event_started = now
                self.event_frames = 1
                self.max_rms = rms
                self.max_peak = peak
                self.max_zcr = zcr
            else:
                # Slowly follow room noise but do not let music spikes raise the
                # baseline quickly enough to hide a nearby clap.
                bounded = min(rms, self.noise_rms * 2.0)
                self.noise_rms = self.noise_rms * 0.985 + bounded * 0.015
            while (
                self.clap_times
                and now - self.clap_times[0] > self.max_clap_interval
            ):
                self.clap_times.popleft()
            return None

        self.event_frames += 1
        self.max_rms = max(self.max_rms, rms)
        self.max_peak = max(self.max_peak, peak)
        self.max_zcr = max(self.max_zcr, zcr)
        if rms < onset_rms * 0.45:
            self.quiet_frames += 1
        else:
            self.quiet_frames = 0
        if self.quiet_frames >= 2 or self.event_frames * FRAME_MS >= 260:
            return self._finish_event(now)
        return None

    def feed_pcm(self, data, now=None, *, suppressed=False):
        return self.feed_metrics(*pcm_metrics(data), now=now, suppressed=suppressed)


class VoiceTriggerEngine:
    """Own the microphone process, wake detector and cached Vosk model."""

    def __init__(self, model_path, *, source="", suppression_callback=None):
        self.model_path = Path(model_path)
        self.source = str(source).strip()
        self.suppression_callback = suppression_callback or (lambda: False)
        self.stop_event = threading.Event()
        self.paused_event = threading.Event()
        self.screen_wake_event = threading.Event()
        self.process = None
        self.model = None
        self.last_capture_cancelled = False
        self.last_cancel_trigger = None

    def available(self):
        return bool(
            self.model_path.is_dir()
            and (self.model_path / "am").is_dir()
            and (self.model_path / "conf").is_dir()
            and shutil_which("parec")
        )

    def _start_capture(self):
        args = [
            "parec", "--raw", f"--rate={SAMPLE_RATE}", "--channels=1",
            "--format=s16le",
        ]
        if self.source:
            args.append(f"--device={self.source}")
        self.process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
        return self.process

    def _stop_capture(self):
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def stop(self):
        self.stop_event.set()
        self._stop_capture()

    def pause(self):
        self.paused_event.set()
        # Releasing parec also releases PipeWire's microphone source, so the
        # desktop privacy indicator disappears while voice control is paused.
        if not self.screen_wake_event.is_set():
            self._stop_capture()

    def resume(self):
        self.paused_event.clear()

    def is_paused(self):
        return self.paused_event.is_set()

    def arm_screen_wake(self):
        """Temporarily listen for an impulse while the display is blanked."""
        self.screen_wake_event.set()

    def disarm_screen_wake(self):
        self.screen_wake_event.clear()
        if self.paused_event.is_set():
            self._stop_capture()

    def screen_wake_armed(self):
        return self.screen_wake_event.is_set()

    def _capture_allowed(self):
        return not self.paused_event.is_set() or self.screen_wake_event.is_set()

    def wait_for_trigger(self):
        while not self.stop_event.is_set():
            while not self._capture_allowed() and not self.stop_event.is_set():
                self.stop_event.wait(0.1)
            if self.stop_event.is_set():
                return None

            wake_only = self.screen_wake_event.is_set()
            detector = (
                ImpulsePatternDetector(
                    calibration_frames=0,
                    onset_rms_floor=800.0,
                    onset_peak_floor=3_000.0,
                    noise_rms_multiplier=3.5,
                    noise_peak_multiplier=9.0,
                    max_clap_interval=2.5,
                    defer_snap_for_double=False,
                )
                if wake_only else ImpulsePatternDetector()
            )
            if wake_only:
                print(
                    "Jarvis: VOICE_TRIGGER chế độ đánh thức màn hình đã sẵn sàng.",
                    flush=True,
                )
            process = self._start_capture()
            # parec/PipeWire can deliver several audio frames in one burst.
            # Gesture timing must follow the PCM timeline, not wall-clock read
            # timestamps, otherwise two real claps can appear simultaneous.
            audio_now = time.monotonic()
            try:
                while not self.stop_event.is_set() and self._capture_allowed():
                    data = process.stdout.read(FRAME_BYTES) if process.stdout else b""
                    if len(data) != FRAME_BYTES:
                        break
                    audio_now += FRAME_MS / 1000
                    trigger = detector.feed_pcm(
                        data,
                        now=audio_now,
                        # Never let Jarvis' own TTS become a wake gesture.
                        suppressed=bool(self.suppression_callback()),
                    )
                    if trigger:
                        return trigger
            finally:
                self._stop_capture()

            # pause() deliberately terminates parec. Stay in this worker and
            # reopen it only after resume(), rather than ending voice control.
            if self._capture_allowed():
                # Also recover from a transient PipeWire/parec disconnect.
                self.stop_event.wait(0.2)
        return None

    def capture_command(self, *, start_timeout=4.0, max_seconds=9.0):
        """Capture one utterance, ending after roughly one second of silence."""
        process = self._start_capture()
        self.last_capture_cancelled = False
        self.last_cancel_trigger = None
        self.last_capture_paused = False
        frames = []
        noise_rms = 100.0
        # The user's close double clap is sometimes captured as one short,
        # high-frequency impulse (the wake detector reports it as ``snap``).
        # During the short listening session either a confirmed snap-shaped
        # impulse or a separated double clap is therefore an explicit cancel.
        cancel_detector = ImpulsePatternDetector(
            calibration_frames=0, recognize_snap=True
        )
        speech_started = False
        silent_frames = 0
        started_at = time.monotonic()
        detector_now = started_at
        try:
            while not self.stop_event.is_set():
                data = process.stdout.read(FRAME_BYTES) if process.stdout else b""
                if len(data) != FRAME_BYTES:
                    if self.paused_event.is_set():
                        self.last_capture_paused = True
                    break
                now = time.monotonic()
                detector_now += FRAME_MS / 1000
                if self.paused_event.is_set() and not self.screen_wake_event.is_set():
                    self.last_capture_paused = True
                    return b""
                rms, _peak, _zcr = pcm_metrics(data)
                cancel_event = cancel_detector.feed_metrics(
                    rms, _peak, _zcr, now=detector_now
                )
                if cancel_event in {"double_clap", "snap"}:
                    self.last_capture_cancelled = True
                    self.last_cancel_trigger = cancel_event
                    return b""

                # The acknowledgement is played through the speakers while
                # capture is already live. Keep detecting the cancel gesture,
                # but never feed Jarvis' own voice into speech recognition.
                if bool(self.suppression_callback()):
                    started_at = now
                    continue

                # The microphone is intentionally configured at a conservative
                # gain, so normal nearby speech can be well below the old 420
                # RMS floor. The rolling baseline still protects against room
                # noise starting an utterance.
                threshold = max(180.0, noise_rms * 2.0)
                if not speech_started:
                    if rms >= threshold:
                        speech_started = True
                        frames.append(data)
                    else:
                        noise_rms = noise_rms * 0.97 + min(rms, noise_rms * 2) * 0.03
                        if now - started_at >= start_timeout:
                            return b""
                else:
                    frames.append(data)
                    if rms < max(140.0, noise_rms * 1.5):
                        silent_frames += 1
                    else:
                        silent_frames = 0
                    if silent_frames * FRAME_MS >= 1_000:
                        break
                    if now - started_at >= max_seconds:
                        break
            return b"".join(frames)
        finally:
            self._stop_capture()

    def transcribe(self, pcm_data):
        if not pcm_data:
            return ""
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        if self.model is None:
            self.model = Model(str(self.model_path))
        recognizer = KaldiRecognizer(self.model, SAMPLE_RATE)
        recognizer.SetWords(False)
        recognizer.AcceptWaveform(pcm_data)
        try:
            result = json.loads(recognizer.FinalResult())
        except (TypeError, ValueError):
            return ""
        return str(result.get("text", "")).strip()


def shutil_which(command):
    # Kept as a tiny seam so availability checks are deterministic in tests.
    from shutil import which
    return which(command)


async def run_voice_trigger_loop(
    engine,
    command_callback,
    *,
    ready_callback=None,
    cancelled_callback=None,
    unrecognized_callback=None,
    trigger_callback=None,
    ready_sound="",
):
    """Wait for wake sounds and dispatch one recognized command at a time."""
    if not engine.available():
        return
    while not engine.stop_event.is_set():
        trigger = await asyncio.to_thread(engine.wait_for_trigger)
        if not trigger or engine.stop_event.is_set():
            return
        print(
            f"Jarvis: VOICE_TRIGGER detected={trigger}; đang nghe lệnh...",
            flush=True,
        )
        if trigger_callback is not None and await trigger_callback(trigger):
            await asyncio.sleep(0.8)
            continue
        if ready_callback is not None:
            await ready_callback(trigger)
        elif ready_sound and Path(ready_sound).is_file() and shutil_which("paplay"):
            await asyncio.to_thread(
                subprocess.run,
                ["paplay", ready_sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
        await asyncio.sleep(0.15)
        pcm_data = await asyncio.to_thread(engine.capture_command)
        if getattr(engine, "last_capture_paused", False):
            print("Jarvis: VOICE_TRIGGER đã được tạm dừng.", flush=True)
            await asyncio.sleep(0.2)
            continue
        if engine.last_capture_cancelled:
            cancel_trigger = getattr(engine, "last_cancel_trigger", None) or "impulse"
            print(
                "Jarvis: VOICE_TRIGGER phiên nghe đã bị hủy "
                f"bằng {cancel_trigger}.",
                flush=True,
            )
            if cancelled_callback is not None:
                await cancelled_callback()
            await asyncio.sleep(0.8)
            continue
        command = await asyncio.to_thread(engine.transcribe, pcm_data)
        if command:
            await command_callback(command, trigger)
        else:
            print(
                "Jarvis: VOICE_TRIGGER không nhận được câu lệnh rõ ràng.",
                flush=True,
            )
            if unrecognized_callback is not None:
                await unrecognized_callback()
        await asyncio.sleep(0.8)
