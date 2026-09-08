"""Receive one authorized Discord user's voice and recognize fixed commands."""

import asyncio
import audioop
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import threading

from discord.ext import voice_recv


_WHISPER_PROCESS = None
_WHISPER_LOCK = threading.Lock()


def _start_shared_whisper_worker():
    global _WHISPER_PROCESS
    if _WHISPER_PROCESS is not None and _WHISPER_PROCESS.poll() is None:
        return _WHISPER_PROCESS
    project_dir = Path(__file__).resolve().parent.parent
    python = project_dir / ".stt-venv" / "bin" / "python"
    worker = project_dir / "scripts" / "whisper_stt_worker.py"
    snapshots = sorted((
        project_dir / ".jarvis_data" / "models" / "faster-whisper"
        / "models--Systran--faster-whisper-small" / "snapshots"
    ).glob("*"))
    if not python.is_file() or not worker.is_file() or not snapshots:
        return None
    process = subprocess.Popen(
        [str(python), str(worker), str(snapshots[-1])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    ready_line = process.stdout.readline() if process.stdout else ""
    try:
        ready = json.loads(ready_line)
    except (TypeError, ValueError):
        ready = {}
    if not ready.get("ready"):
        process.terminate()
        return None
    _WHISPER_PROCESS = process
    print("Jarvis: DISCORD_VOICE Whisper đã sẵn sàng.", flush=True)
    return process


def recognize_whisper_pcm(mono_pcm):
    """Recognize one 16 kHz mono PCM command using the shared local worker."""
    global _WHISPER_PROCESS
    with _WHISPER_LOCK:
        process = _start_shared_whisper_worker()
        if process is None or process.stdin is None or process.stdout is None:
            return None
        request = json.dumps({
            "pcm": base64.b64encode(mono_pcm).decode("ascii"),
        })
        try:
            process.stdin.write(request + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())
        except (BrokenPipeError, OSError, TypeError, ValueError):
            _WHISPER_PROCESS = None
            return None


def decode_voice_message_audio(encoded_audio):
    """Decode a Discord voice attachment to signed 16-bit mono 16 kHz PCM."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0", "-vn", "-sn", "-dn", "-ac", "1",
                "-ar", "16000", "-f", "s16le", "pipe:1",
            ],
            input=bytes(encoded_audio),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("không giải mã được tin nhắn thoại") from error
    if result.returncode != 0 or not result.stdout:
        raise ValueError("tin nhắn thoại không có âm thanh hợp lệ")
    return result.stdout


_DEVICE_COMMAND_ALIASES = {
    "bật thiết bị": "bật thiết bị",
    "tắt thiết bị": "tắt thiết bị",
    "bật máy lạnh": "bật máy lạnh",
    "tắt máy lạnh": "tắt máy lạnh",
    "bật quạt": "bật quạt",
    "tắt quạt": "tắt quạt",
    # Observed from the owner's Discord microphone: "tắt quạt" was decoded
    # as "tắt bài" with otherwise good Whisper confidence.
    "tắt bài": "tắt quạt",
    "mở quạt": "bật quạt",
    "dừng quạt": "tắt quạt",
    "ngừng quạt": "tắt quạt",
    "bật máy quạt": "bật quạt",
    "tắt máy quạt": "tắt quạt",
    "bật pc": "bật pc",
    "tắt pc": "tắt pc",
    "bật p c": "bật pc",
    "tắt p c": "tắt pc",
    "bạc tích bay": "bật thiết bị",
    "mặc thức bệnh": "bật thiết bị",
}

_GENERAL_COMMAND_ALIASES = {
    # Observed Vietnamese Whisper substitutions from this user's microphone.
    "my youtube": "mở youtube",
    "mà youtube": "mở youtube",
    "mờ youtube": "mở youtube",
    "mở you tube": "mở youtube",
    # Exact observed substitution for the spoken command "tắt YouTube".
    # Keep plain "thank you" separate as a friendly conversation phrase.
    "thank you too": "tắt youtube",
    "mở za lô": "mở zalo",
    "mở gia lô": "mở zalo",
    "mở yellow": "mở zalo",
    "tắt yellow": "tắt zalo",
    "mở dì lâu": "mở zalo",
    "mở di lâu": "mở zalo",
    "mở zi lâu": "mở zalo",
    "mở gì lâu": "mở zalo",
    "mở dè lô": "mở zalo",
    "mở đè lô": "mở zalo",
    "bật dè lô": "mở zalo",
    "bật đè lô": "mở zalo",
    "tắt dì lâu": "tắt zalo",
    "tắt di lâu": "tắt zalo",
    "tắt zi lâu": "tắt zalo",
    "tắt gì lâu": "tắt zalo",
    "tắt dè lô": "tắt zalo",
    "tắt đè lô": "tắt zalo",
    "tắt ghi lô": "tắt zalo",
    "tắt dê lô": "tắt zalo",
    "mở ghi lô": "mở zalo",
    "mở dê lô": "mở zalo",
    "mở g mail": "mở gmail",
    "mở ghi mail": "mở gmail",
    "mở vi ét code": "mở vscode",
    "mở visual studio code": "mở vscode",
    "mở git hub": "mở github",
    "mở gít hắp": "mở github",
    "mở gít hấp": "mở github",
    "mở ghít hắp": "mở github",
    "git hub": "mở github",
    "gít hắp": "mở github",
    "gít hấp": "mở github",
    "log it up": "mở github",
    "loggit hub": "mở github",
    "barh github": "mở github",
    "bargit hub": "mở github",
    "bargith hub": "mở github",
    "mà gít hấp": "mở github",
    "mà gít hắp": "mở github",
    "mà git hub": "mở github",
    "mà gít ạp": "mở github",
    "má gít ạp": "mở github",
    "tag github": "tắt github",
    "tag gighub": "tắt github",
    "thank you github": "tắt github",
    "tắt git hub": "tắt github",
    "tắt gít hấp": "tắt github",
    "tắt gít hắp": "tắt github",
    "tắt ghít hắp": "tắt github",
    "đóng gít hấp": "tắt github",
    "đóng gít hắp": "tắt github",
    "bật thấy bay": "bật thiết bị",
    "bật thích bẫy": "bật thiết bị",
    "bật thích bị": "bật thiết bị",
    "bật thiết bị": "bật thiết bị",
    "tắt thấy bay": "tắt thiết bị",
    "tắt thích bẫy": "tắt thiết bị",
    "tắt thích bị": "tắt thiết bị",
    "tắt thiết bị": "tắt thiết bị",
    "bật máy lạnh": "bật máy lạnh",
    "tắt máy lạnh": "tắt máy lạnh",
    "bật pc": "bật pc",
    "tắt pc": "tắt pc",
    "bật p c": "bật pc",
    "tắt p c": "tắt pc",
}

_DEVICE_PHONETIC_TAILS = {
    "thiết bị", "thích bị", "thích bậy", "thích bây", "thích bay",
    "tích bị", "tích bậy", "tích bây", "tích bay",
    "thấy bay", "thích bẫy", "thích quay", "thích mậy", "thích máy",
    "thích bệnh", "thức bệnh", "thích bày", "thích bài",
    "tạch bay", "tạch bày",
}

_ACTION_PHONETIC_ALIASES = {
    "tạch": "tắt",
    "tạc": "tắt",
    "tắc": "tắt",
    "thắt": "tắt",
}


def canonicalize_whisper_command(command):
    """Repair observed substitutions only in an unambiguous command shape."""
    command = str(command).strip().casefold()
    command = re.sub(
        r"\bthời\s+(?:thiết|tiếc|tiếp)\b", "thời tiết", command
    )
    command = re.sub(
        r"^(?:tạm tích|thời thích)\b", "thời tiết", command
    )
    weather_prefix = re.match(
        r"^(?:thời tiết|hôm nay thời tiết|tuần này thời tiết|"
        r"tuần tới thời tiết|(?:1|một) tuần nữa thời tiết)\b",
        command,
    )
    if weather_prefix:
        if "tuần tới" in command:
            return "thời tiết tuần tới thế nào"
        if re.search(r"\b(?:1|một) tuần nữa\b", command):
            return "thời tiết một tuần nữa như thế nào"
        if "tuần này" in command:
            return "thời tiết tuần này thế nào"
        if "hôm nay" in command:
            return "thời tiết hôm nay thế nào"
    direct = _GENERAL_COMMAND_ALIASES.get(command)
    if direct is not None:
        return direct
    tokens = command.split()
    if tokens and tokens[0] in _ACTION_PHONETIC_ALIASES:
        tokens[0] = _ACTION_PHONETIC_ALIASES[tokens[0]]
        command = " ".join(tokens)
    device_action = {
        "bật": "bật",
        "tắt": "tắt",
        # Observed substitutions are accepted only with a known two-word
        # phonetic form of "thiết bị", never as global action aliases.
        "mở": "bật",
        "mặc": "bật",
    }.get(tokens[0] if tokens else "")
    if len(tokens) == 3 and device_action is not None:
        if " ".join(tokens[1:]) in _DEVICE_PHONETIC_TAILS:
            return f"{device_action} thiết bị"
    return _DEVICE_COMMAND_ALIASES.get(command, command)

# "Jarvis" is out-of-vocabulary in the Vietnamese Vosk model.  These are the
# in-vocabulary Vietnamese token sequences it emits for the same spoken wake
# word.  Every executable grammar phrase must start with one of them.
_JARVIS_WAKE_ALIASES = (
    "gia vít", "gia viết", "cha vít", "cha viết", "da vít", "da viết",
    "giảm vệ", "cháu vết", "chá vết", "chá vất",
)
DISCORD_VOICE_COMMAND_ALIASES = {
    f"{wake} {phrase}": command
    for wake in _JARVIS_WAKE_ALIASES
    for phrase, command in _DEVICE_COMMAND_ALIASES.items()
}


def starts_with_jarvis_wake_phrase(text):
    """Return whether Vosk text begins with a known Jarvis pronunciation."""
    tokens = re.sub(
        r"[^0-9a-zà-ỹđ]+", " ", str(text).strip().casefold()
    ).split()
    if tokens[:1] == ["jarvis"]:
        return True
    return any(
        tokens[:len(wake.split())] == wake.split()
        for wake in _JARVIS_WAKE_ALIASES
    )


def match_whisper_voice_command(result):
    """Extract one wake-prefixed command from a confident Whisper result."""
    if result.get("error"):
        return None
    if float(result.get("no_speech_probability", 1.0)) >= 0.60:
        return None
    average_log_probability = float(
        result.get("average_log_probability", -99.0)
    )
    if average_log_probability < -1.0:
        return None
    normalized = " ".join(re.sub(
        r"[^0-9a-zà-ỹđ]+", " ", str(result.get("text", "")).casefold()
    ).split())
    tokens = normalized.split()
    wake_length = None
    if tokens[:1] == ["jarvis"]:
        wake_length = 1
    else:
        for wake in _JARVIS_WAKE_ALIASES:
            wake_tokens = wake.split()
            if tokens[:len(wake_tokens)] == wake_tokens:
                wake_length = len(wake_tokens)
                break
    # Observed clipping case: Whisper decoded the spoken wake word "Jarvis"
    # as "thời tiết" before one exact device command. Limit this recovery to
    # fixed device commands so a real weather sentence is never executed as
    # an unrelated action.
    if wake_length is None and tokens[:2] == ["thời", "tiết"]:
        recovered = canonicalize_whisper_command(" ".join(tokens[2:]))
        if recovered in set(_DEVICE_COMMAND_ALIASES.values()):
            wake_length = 2
    if wake_length is None or len(tokens) <= wake_length:
        return None
    command_tokens = tokens[wake_length:]
    # If the owner repeats "Jarvis" while retrying, keep only the first exact
    # fixed command. A repeated transcript with unknown text remains rejected.
    repeated_wake_at = None
    for index in range(len(command_tokens)):
        if command_tokens[index] == "jarvis":
            repeated_wake_at = index
            break
        for wake in _JARVIS_WAKE_ALIASES:
            wake_tokens = wake.split()
            if command_tokens[index:index + len(wake_tokens)] == wake_tokens:
                repeated_wake_at = index
                break
        if repeated_wake_at is not None:
            break
    if repeated_wake_at is not None:
        first_command = canonicalize_whisper_command(
            " ".join(command_tokens[:repeated_wake_at])
        )
        fixed_commands = (
            set(_GENERAL_COMMAND_ALIASES.values())
            | set(_DEVICE_COMMAND_ALIASES.values())
        )
        if first_command not in fixed_commands:
            return None
        command_tokens = command_tokens[:repeated_wake_at]
    if (
        len(command_tokens) >= 2
        and command_tokens[0] in {"bật", "tắt"}
        and command_tokens[1] == command_tokens[0]
    ):
        command_tokens = command_tokens[1:]
    command = " ".join(command_tokens)
    command = canonicalize_whisper_command(command)
    fixed_commands = (
        set(_GENERAL_COMMAND_ALIASES.values())
        | set(_DEVICE_COMMAND_ALIASES.values())
    )
    if average_log_probability < -0.85 and command not in fixed_commands:
        return None
    return command


def match_whisper_chat_command(result):
    """Extract an intentional command from an owner's chat voice message."""
    if not isinstance(result, dict) or result.get("error"):
        return None
    if float(result.get("no_speech_probability", 1.0)) >= 0.60:
        return None
    if float(result.get("average_log_probability", -99.0)) < -0.85:
        return None
    wake_command = match_whisper_voice_command(result)
    if wake_command is not None:
        return wake_command
    normalized = " ".join(re.sub(
        r"[^0-9a-zà-ỹđ]+", " ", str(result.get("text", "")).casefold()
    ).split())
    if not normalized:
        return None
    return canonicalize_whisper_command(normalized)


def match_discord_voice_command(
    result, *, minimum_wake_confidence=0.72,
    minimum_command_confidence=0.62, minimum_average_confidence=0.72,
):
    """Extract a confident command after a mandatory Jarvis wake phrase."""
    recognized = str(result.get("text", "")).strip().casefold()
    words = result.get("result", [])
    if not recognized or not words:
        return None
    spoken_words = " ".join(
        str(word.get("word", "")).strip().casefold()
        for word in words if isinstance(word, dict)
    ).strip()
    confidences = [
        float(word.get("conf", 0.0))
        for word in words if isinstance(word, dict)
    ]
    tokens = recognized.split()
    if spoken_words != recognized or len(confidences) != len(tokens):
        return None
    wake_length = None
    for wake in _JARVIS_WAKE_ALIASES:
        wake_tokens = wake.split()
        if tokens[:len(wake_tokens)] == wake_tokens:
            wake_length = len(wake_tokens)
            break
    if wake_length is None or len(tokens) <= wake_length:
        return None
    duration = max(
        0.0,
        float(words[-1].get("end", 0.0))
        - float(words[0].get("start", 0.0)),
    )
    wake_confidences = confidences[:wake_length]
    command_confidences = confidences[wake_length:]
    if (
        min(wake_confidences) < minimum_wake_confidence
        or min(command_confidences) < minimum_command_confidence
        or sum(confidences) / len(confidences) < minimum_average_confidence
        or not 0.35 <= duration <= 9.5
    ):
        return None
    command_tokens = tokens[wake_length:]
    # Vosk occasionally duplicates a short initial verb at a word boundary
    # (for example "tắt tắt quạt").  Removing only an exact adjacent
    # duplicate is deterministic and never changes bật into tắt or vice versa.
    if (
        len(command_tokens) >= 2
        and command_tokens[0] in {"bật", "tắt"}
        and command_tokens[1] == command_tokens[0]
    ):
        command_tokens = command_tokens[1:]
    spoken_command = " ".join(command_tokens)
    return _DEVICE_COMMAND_ALIASES.get(spoken_command, spoken_command)


class DiscordVoiceCommandSink(voice_recv.AudioSink):
    """Collect authorized PCM utterances and dispatch exact Vosk commands."""

    INPUT_RATE = 48_000
    INPUT_CHANNELS = 2
    SAMPLE_WIDTH = 2
    OUTPUT_RATE = 16_000
    MAX_UTTERANCE_SECONDS = 10
    UTTERANCE_END_DELAY_SECONDS = 0.80

    def __init__(
        self, *, owner_id, model_path, loop, command_callback,
        heard_callback=None,
    ):
        super().__init__()
        self.owner_id = int(owner_id)
        self.model_path = Path(model_path)
        self.loop = loop
        self.command_callback = command_callback
        self.heard_callback = heard_callback
        self.buffer = bytearray()
        self.buffer_lock = threading.Lock()
        self.collecting = False
        self.stop_timer = None
        self.closed = False
        self.model = None
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="discord-voice-vosk"
        )

    def _start_whisper(self):
        return _start_shared_whisper_worker()

    def _recognize_whisper(self, mono_pcm):
        return recognize_whisper_pcm(mono_pcm)

    def wants_opus(self):
        return False

    def _is_owner(self, user):
        return getattr(user, "id", None) == self.owner_id

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_start(self, member):
        if not self._is_owner(member) or self.closed:
            return
        with self.buffer_lock:
            if self.stop_timer is not None:
                self.stop_timer.cancel()
                self.stop_timer = None
            # Discord can deliver the first PCM packets before its speaking
            # event.  Keep those packets or the wake word at the start of the
            # utterance ("Jarvis") gets clipped intermittently.
            if not self.collecting:
                self.buffer.clear()
                self.collecting = True

    def write(self, user, data):
        if not self._is_owner(user) or self.closed:
            return
        pcm = bytes(getattr(data, "pcm", b""))
        if not pcm:
            return
        if not getattr(self, "received_first_packet", False):
            self.received_first_packet = True
            print(
                f"Jarvis: DISCORD_VOICE PCM bắt đầu owner={self.owner_id}",
                flush=True,
            )
        max_bytes = (
            self.INPUT_RATE * self.INPUT_CHANNELS * self.SAMPLE_WIDTH
            * self.MAX_UTTERANCE_SECONDS
        )
        with self.buffer_lock:
            if self.stop_timer is not None:
                self.stop_timer.cancel()
                self.stop_timer = None
            if not self.collecting:
                self.buffer.clear()
                self.collecting = True
            remaining = max_bytes - len(self.buffer)
            if remaining > 0:
                self.buffer.extend(pcm[:remaining])

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_stop(self, member):
        if not self._is_owner(member) or self.closed:
            return
        with self.buffer_lock:
            if self.stop_timer is not None:
                self.stop_timer.cancel()
            # Discord may briefly signal "stop" between the wake word and the
            # command.  Wait through a natural short pause and merge both
            # pieces into one utterance instead of decoding "Jarvis" alone.
            self.stop_timer = threading.Timer(
                self.UTTERANCE_END_DELAY_SECONDS,
                self._finish_utterance,
            )
            self.stop_timer.daemon = True
            self.stop_timer.start()

    def _finish_utterance(self):
        if self.closed:
            return
        with self.buffer_lock:
            pcm = bytes(self.buffer)
            self.buffer.clear()
            self.collecting = False
            self.stop_timer = None
        minimum_bytes = int(
            self.INPUT_RATE * self.INPUT_CHANNELS * self.SAMPLE_WIDTH * 0.35
        )
        if len(pcm) >= minimum_bytes:
            self.executor.submit(self._recognize_and_dispatch, pcm)

    def _recognize_and_dispatch(self, stereo_pcm):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        if self.closed:
            return
        mono_pcm = audioop.tomono(stereo_pcm, self.SAMPLE_WIDTH, 0.5, 0.5)
        mono_pcm, _state = audioop.ratecv(
            mono_pcm,
            self.SAMPLE_WIDTH,
            1,
            self.INPUT_RATE,
            self.OUTPUT_RATE,
            None,
        )
        # Give the decoder a clean word boundary at both ends.  This is
        # especially useful for a wake word spoken immediately after Discord
        # opens the user's voice stream.
        boundary_silence = b"\x00\x00" * int(self.OUTPUT_RATE * 0.12)
        mono_pcm = boundary_silence + mono_pcm + boundary_silence
        result = self._recognize_whisper(mono_pcm)
        engine = "whisper"
        if result is None:
            # Safe fallback: Vosk is constrained to known device phrases.  It
            # cannot route arbitrary background speech as a chat command.
            if self.model is None:
                self.model = Model(str(self.model_path))
            grammar = json.dumps(
                sorted(DISCORD_VOICE_COMMAND_ALIASES) + ["[unk]"],
                ensure_ascii=False,
            )
            recognizer = KaldiRecognizer(self.model, self.OUTPUT_RATE, grammar)
            recognizer.SetWords(True)
            recognizer.AcceptWaveform(mono_pcm)
            try:
                result = json.loads(recognizer.FinalResult())
            except (TypeError, ValueError):
                return
            command = match_discord_voice_command(result)
            engine = "vosk-fallback"
        else:
            command = match_whisper_voice_command(result)
        heard = str(result.get("text", "")).strip()
        confidence_log = " ".join(
            f"{word.get('word', '?')}:{float(word.get('conf', 0.0)):.2f}"
            for word in result.get("result", [])
            if isinstance(word, dict)
        )
        if engine == "whisper":
            confidence_log = (
                f"avg_logprob={float(result.get('average_log_probability', -99)):.2f} "
                f"no_speech={float(result.get('no_speech_probability', 1)):.2f}"
            )
        print(
            f"Jarvis: DISCORD_VOICE heard={heard or '<empty>'} "
            f"engine={engine} accepted={int(command is not None)} "
            f"command={command or '<none>'} "
            f"confidence=[{confidence_log}]",
            flush=True,
        )
        if self.closed:
            return
        if (
            heard
            and heard != "[unk]"
            and self.heard_callback is not None
            and (command is not None or starts_with_jarvis_wake_phrase(heard))
        ):
            heard_future = asyncio.run_coroutine_threadsafe(
                self.heard_callback(heard, command), self.loop
            )

            def log_heard_failure(done):
                try:
                    done.result()
                except Exception as error:
                    print(
                        "Jarvis: DISCORD_VOICE heard callback lỗi: "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )

            heard_future.add_done_callback(log_heard_failure)
        if command is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self.command_callback(command), self.loop
        )

        def log_failure(done):
            try:
                done.result()
            except Exception as error:
                print(
                    "Jarvis: DISCORD_VOICE callback lỗi: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

        future.add_done_callback(log_failure)

    def cleanup(self):
        if getattr(self, "closed", True):
            return
        self.closed = True
        with self.buffer_lock:
            self.buffer.clear()
            if self.stop_timer is not None:
                self.stop_timer.cancel()
                self.stop_timer = None
        self.executor.shutdown(wait=False, cancel_futures=True)


def start_discord_voice_listening(
    voice_client, *, owner_id, model_path, loop, command_callback,
    heard_callback=None,
):
    """Attach the Jarvis speech sink to a connected VoiceRecvClient."""
    if voice_client.is_listening():
        return getattr(voice_client, "jarvis_command_sink", None)
    sink = DiscordVoiceCommandSink(
        owner_id=owner_id,
        model_path=model_path,
        loop=loop,
        command_callback=command_callback,
        heard_callback=heard_callback,
    )

    def listener_finished(error):
        if error is None:
            print("Jarvis: DISCORD_VOICE listener đã dừng.", flush=True)
        else:
            print(
                "Jarvis: DISCORD_VOICE listener lỗi: "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

    voice_client.listen(sink, after=listener_finished)
    voice_client.jarvis_command_sink = sink
    return sink
