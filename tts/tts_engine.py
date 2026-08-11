#!/usr/bin/env python3

from pathlib import Path
import subprocess
import sys


# ==========================================================
# PATH
# ==========================================================

BASE_DIR = Path.home() / "Projects" / "jarvis"
TTS_DIR = BASE_DIR / "tts"
VIE_NEU_DIR = TTS_DIR / "VieNeu-TTS"

sys.path.insert(0, str(VIE_NEU_DIR / "src"))

from vieneu import Vieneu


# ==========================================================
# FILE
# ==========================================================

VOICE_FILE = TTS_DIR / "voices" / "jarvis_voice.wav"
OUTPUT_FILE = TTS_DIR / "output.wav"


# ==========================================================
# LOAD MODEL - CHỈ LOAD 1 LẦN
# ==========================================================

print("Jarvis TTS: Đang tải model...", flush=True)

tts = Vieneu()

print("Jarvis TTS: Model đã sẵn sàng.", flush=True)


# ==========================================================
# SPEAK
# ==========================================================

def speak(text):
    text = str(text).strip()

    if not text:
        return

    print(f"Jarvis TTS: {text}", flush=True)

    try:
        audio = tts.infer(
            text,
            ref_audio=str(VOICE_FILE),
            denoise=False,
        )

        tts.save(audio, str(OUTPUT_FILE))

        subprocess.run(
            ["aplay", "-q", str(OUTPUT_FILE)],
            check=False,
        )

    except Exception as error:
        print(f"Jarvis TTS ERROR: {error}", flush=True)


# ==========================================================
# MAIN
# ==========================================================

if __name__ == "__main__":
    # Chế độ cũ để test trực tiếp:
    # python tts_engine.py "Xin chào"
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        speak(text)

    # Worker mode:
    # Jarvis mở file này 1 lần rồi gửi từng câu qua stdin.
    # VieNeu không load lại giữa các câu.
    else:
        print(
            "Jarvis TTS: Worker đang chờ nội dung...",
            flush=True,
        )

        try:
            for line in sys.stdin:
                text = line.strip()

                if not text:
                    continue

                if text == "__EXIT__":
                    print(
                        "Jarvis TTS: Đang tắt worker...",
                        flush=True,
                    )
                    break

                speak(text)

        except KeyboardInterrupt:
            pass

        print(
            "Jarvis TTS: Worker đã dừng.",
            flush=True,
        )
