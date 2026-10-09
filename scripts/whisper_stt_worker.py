#!/usr/bin/env python3
"""Persistent Faster-Whisper worker for Discord 16 kHz mono PCM."""

import base64
import json
import sys

import numpy as np
from faster_whisper import WhisperModel


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: whisper_stt_worker.py MODEL_DIRECTORY")
    model = WhisperModel(
        sys.argv[1], device="cpu", compute_type="int8", cpu_threads=4,
    )
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            pcm = base64.b64decode(request["pcm"])
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            audio /= 32768.0
            segments, info = model.transcribe(
                audio,
                language="vi",
                beam_size=5,
                best_of=5,
                repetition_penalty=1.12,
                no_repeat_ngram_size=3,
                max_new_tokens=64,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 250},
                hotwords=(
                    "Jarvis GitHub Git Hub gít hắp gít hấp Bargit Hub "
                    "Tắt GitHub Tắt gít hấp Thank you GitHub YouTube "
                    "Thank you Thank you too Zalo "
                    "Gmail Chrome VS Code SoundCloud Facebook Google ChatGPT "
                    "Drive Calendar Terminal "
                    "Downloads thiết bị quạt máy lạnh PC âm lượng video "
                    "màn hình hệ thống bộ nhớ file thư mục thời tiết "
                    "dự báo gần tôi hôm nay tuần này tuần tới "
                    "nhiệt độ khả năng mưa"
                ),
                initial_prompt=(
                    "Jarvis. Mở Git Hub, viết là GitHub. Mở GitHub. "
                    "Tắt GitHub. Tắt gít hấp, nghĩa là tắt GitHub. "
                    "Thank you GitHub. "
                    "GitHub không phải YouTube. Mở YouTube. Tắt YouTube. "
                    "Thank you too. Thank you. "
                    "Mở Za-lô, viết là Zalo. "
                    "Tắt Zalo. Mở Gmail. Mở Chrome. "
                    "Mở VS Code. Mở SoundCloud. Mở Facebook. Mở Google. "
                    "Bật thiết-bị. Tắt thiết-bị. Bật quạt. Tắt quạt. "
                    "Mở quạt. Dừng quạt. Bật máy lạnh. Tắt máy lạnh. "
                    "Bật PC. Tắt PC. Tăng âm lượng. Giảm âm lượng. "
                    "Tắt âm lượng. Mở video. Dừng video. Phát tiếp video. "
                    "Thời tiết hôm nay thế nào. Thời tiết tuần này thế nào. "
                    "Thời tiết một tuần nữa như thế nào. "
                    "Thời tiết tuần tới thế nào. Hôm nay thời tiết thế nào. "
                    "Tuần này thời tiết thế nào. Tuần tới thời tiết thế nào. "
                    "Trạng thái hệ thống."
                ),
            )
            segments = list(segments)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            average_log_probability = (
                sum(segment.avg_logprob for segment in segments) / len(segments)
                if segments else -99.0
            )
            no_speech_probability = (
                max(segment.no_speech_prob for segment in segments)
                if segments else 1.0
            )
            response = {
                "text": text,
                "average_log_probability": average_log_probability,
                "no_speech_probability": no_speech_probability,
                "language_probability": info.language_probability,
            }
        except Exception as error:
            response = {
                "error": f"{type(error).__name__}: {error}",
                "text": "",
            }
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
