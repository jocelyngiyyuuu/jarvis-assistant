#!/usr/bin/env python3

from pathlib import Path
import sys

from tts_playback import play_wav
from tts_limits import max_new_frames_for_text


# ==========================================================
# PATH
# ==========================================================

TTS_DIR = Path(__file__).resolve().parent
VIE_NEU_DIR = TTS_DIR / "VieNeu-TTS"

sys.path.insert(0, str(TTS_DIR))
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
        max_new_frames = max_new_frames_for_text(text)
        audio = tts.infer(
            text,
            ref_audio=str(VOICE_FILE) if VOICE_FILE.exists() else None,
            denoise=False,
            use_ref_codes=False,
            max_new_frames=max_new_frames,
        )

        tts.save(audio, str(OUTPUT_FILE))
        duration = len(audio) / getattr(tts, "sample_rate", 48000)
        print(
            f"Jarvis TTS: {duration:.1f}s ({max_new_frames} frames)",
            flush=True,
        )
        play_wav(OUTPUT_FILE)

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
