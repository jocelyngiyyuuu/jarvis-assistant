"""Giới hạn độ dài sinh giọng VieNeu v3 Turbo."""

# Mỗi frame ~80 ms; mặc định SDK là 300 frame = 24 giây.
FRAME_HZ = 12.5
MIN_FRAMES = 36
MAX_FRAMES = 300


def max_new_frames_for_text(text):
    """Tính trần frame theo độ dài câu để TTS không nói đến hết 24 giây."""
    chars = max(len(str(text).strip()), 1)
    # ~10 ký tự/giây, thêm 2.2 lần cho ngắt hơi; không thấp hơn ~3 giây.
    seconds = min(MAX_FRAMES / FRAME_HZ, max(MIN_FRAMES / FRAME_HZ, chars / 10 * 2.2))
    return max(MIN_FRAMES, min(MAX_FRAMES, int(seconds * FRAME_HZ)))
