from pathlib import Path
import shutil
import subprocess


def play_wav(path):
    """Phát WAV trên loa máy, ưu tiên Pulse/PipeWire rồi mới ALSA."""
    wav = str(path)
    players = (
        ("paplay", [wav]),
        ("pw-play", [wav]),
        ("aplay", ["-q", wav]),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", wav]),
    )
    failures = []

    for binary, args in players:
        if shutil.which(binary) is None:
            continue
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        detail = (result.stderr or result.stdout or "").strip()
        failures.append(f"{binary}: {detail or f'exit {result.returncode}'}")

    if failures:
        print(
            "Jarvis TTS ERROR: Không phát được loa máy. " + "; ".join(failures),
            flush=True,
        )
    else:
        print(
            "Jarvis TTS ERROR: Không tìm thấy paplay, pw-play, aplay hoặc ffplay.",
            flush=True,
        )
    return False
