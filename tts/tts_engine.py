#!/usr/bin/env python3

from pathlib import Path
import json
import numpy as np
import os
import sys

from tts_playback import play_wav
from tts_limits import max_new_frames_for_text, split_text_for_tts


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

def find_cached_onnx_model():
    """Find a complete local VieNeu ONNX snapshot without contacting HF."""
    hf_home = Path(os.getenv(
        "HF_HOME", str(Path.home() / ".cache" / "huggingface")
    )).expanduser()
    snapshots = (
        hf_home / "hub"
        / "models--pnnbao-ump--VieNeu-TTS-v3-Turbo"
        / "snapshots"
    )
    required = {
        "config.json", "tokenizer.json", "vieneu_prefill.onnx",
        "vieneu_decode_step.onnx", "vieneu_acoustic_cached.onnx",
        "vieneu_backbone_shared.data", "vieneu_v3_heads.npz",
    }
    for candidate in sorted(snapshots.glob("*/onnx_int8"), reverse=True):
        if all((candidate / filename).is_file() for filename in required):
            return candidate
    raise RuntimeError(
        "Không tìm thấy snapshot VieNeu ONNX hoàn chỉnh trong cache local."
    )


tts = Vieneu(
    backend="onnx",
    onnx_dir=str(find_cached_onnx_model()),
)

print("Jarvis TTS: Model đã sẵn sàng.", flush=True)


# ==========================================================
# SPEAK
# ==========================================================

def speak(text, *, play_local=True):
    text = str(text).strip()

    if not text:
        return

    print(f"Jarvis TTS: {text}", flush=True)

    try:
        chunks = split_text_for_tts(text)
        rendered = []
        frame_limits = []
        for index, chunk in enumerate(chunks):
            max_new_frames = max_new_frames_for_text(chunk)
            frame_limits.append(max_new_frames)
            rendered.append(np.asarray(tts.infer(
                chunk,
                ref_audio=(
                    str(VOICE_FILE) if VOICE_FILE.exists() else None
                ),
                denoise=False,
                use_ref_codes=False,
                # Lấy mẫu ổn định hơn để hạn chế model tự nói thêm.
                temperature=0.6,
                top_k=20,
                top_p=0.9,
                repetition_penalty=1.3,
                max_new_frames=max_new_frames,
            )).reshape(-1))
            if index + 1 < len(chunks):
                # Preserve a short, audible boundary between forecast days
                # after independently synthesized chunks are joined.
                rendered.append(np.zeros(
                    int(getattr(tts, "sample_rate", 48000) * 0.12),
                    dtype=np.float32,
                ))

        audio = np.concatenate(rendered)

        tts.save(audio, str(OUTPUT_FILE))
        duration = len(audio) / getattr(tts, "sample_rate", 48000)
        print(
            f"Jarvis TTS: {duration:.1f}s "
            f"({len(chunks)} đoạn, {sum(frame_limits)} frames)",
            flush=True,
        )
        if play_local:
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
                play_local = True

                if not text:
                    continue

                if text == "__EXIT__":
                    print(
                        "Jarvis TTS: Đang tắt worker...",
                        flush=True,
                    )
                    break

                if text.startswith("{"):
                    try:
                        request = json.loads(text)
                        text = str(request.get("text", "")).strip()
                        play_local = bool(request.get("play_local", True))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass

                speak(text, play_local=play_local)

        except KeyboardInterrupt:
            pass

        print(
            "Jarvis TTS: Worker đã dừng.",
            flush=True,
        )
