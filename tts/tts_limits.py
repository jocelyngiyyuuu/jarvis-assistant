"""Giới hạn độ dài sinh giọng VieNeu v3 Turbo."""

import math
import re


# Mỗi frame ~80 ms; mặc định SDK là 300 frame = 24 giây.
FRAME_HZ = 12.5
# Câu xác nhận ngắn chỉ cần khoảng 1,5–2 giây. Trần tối thiểu cũ là 36
# frame (~2,9 giây), khiến model còn thời gian tự sinh thêm từ sau câu thật.
MIN_FRAMES = 22
MAX_FRAMES = 300


def split_text_for_tts(text, max_chars=120):
    """Split long text at sentence boundaries without dropping any words."""
    text = " ".join(str(text).split())
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?;])\s+", text)
    chunks = []
    current = ""
    for sentence in sentences:
        piece = ""
        pieces = []
        for word in sentence.split():
            candidate = f"{piece} {word}".strip()
            if piece and len(candidate) > max_chars:
                pieces.append(piece)
                piece = word
            else:
                piece = candidate
        if piece:
            pieces.append(piece)
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def max_new_frames_for_text(text):
    """Tính trần frame theo độ dài câu để TTS không nói đến hết 24 giây."""
    value = str(text).strip()
    chars = max(len(value), 1)
    words = max(len(re.findall(r"\S+", value)), 1)

    # Ước lượng theo cả ký tự và số từ để không cắt câu có nhiều từ ngắn.
    # Chỉ cộng khoảng đệm nhỏ cho ngắt hơi; max_new_frames là trần an toàn,
    # không phải độ dài mà model bắt buộc phải sinh đủ.
    seconds = max(chars / 11.0 + 0.55, words / 2.8 + 0.5)
    seconds = min(MAX_FRAMES / FRAME_HZ, max(MIN_FRAMES / FRAME_HZ, seconds))
    return max(
        MIN_FRAMES,
        min(MAX_FRAMES, int(math.ceil(seconds * FRAME_HZ))),
    )
