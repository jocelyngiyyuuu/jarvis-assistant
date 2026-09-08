"""Find a newly drawn marker of any contrasting color in a screenshot."""

from pathlib import Path
import subprocess


class MarkedScreenshotError(ValueError):
    pass


def _decode_rgb(path):
    try:
        identified = subprocess.run(
            ["identify", "-format", "%w %h", str(path)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        width_text, height_text = identified.stdout.strip().split()
        width, height = int(width_text), int(height_text)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
        raise MarkedScreenshotError("Không đọc được kích thước ảnh.") from error
    if (identified.returncode != 0 or width < 1 or height < 1
            or width * height > 20_000_000):
        raise MarkedScreenshotError("Kích thước ảnh không hợp lệ hoặc quá lớn.")
    try:
        decoded = subprocess.run(
            ["convert", str(path), "-alpha", "off", "-depth", "8", "rgb:-"],
            capture_output=True, check=False, timeout=20,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as error:
        raise MarkedScreenshotError("Không giải mã được ảnh đánh dấu.") from error
    expected = width * height * 3
    if decoded.returncode != 0 or len(decoded.stdout) != expected:
        raise MarkedScreenshotError("Dữ liệu ảnh đánh dấu không hợp lệ.")
    return width, height, decoded.stdout


def detect_added_red_marker_rgb(original, marked, width, height):
    """Return the centroid of one new compact change in two RGB buffers."""
    if len(original) != width * height * 3 or len(marked) != len(original):
        raise MarkedScreenshotError("Ảnh đánh dấu không cùng kích thước ảnh gốc.")

    # Estimate JPEG/re-encoding noise before looking for a mark. The threshold
    # therefore adapts to both lossless PNG edits and images exported by a
    # phone editor. It is capped so a tiny high-contrast dot remains visible.
    sampled_differences = []
    sampled_large_changes = 0
    sample_step = max(1, (width * height) // 20_000)
    for pixel_index in range(0, width * height, sample_step):
        offset = pixel_index * 3
        channel_differences = [
            abs(marked[offset + channel] - original[offset + channel])
            for channel in range(3)
        ]
        difference = sum(channel_differences) / 3
        sampled_differences.append(difference)
        sampled_large_changes += difference >= 60

    sampled_differences.sort()
    sample_count = max(1, len(sampled_differences))
    average_difference = sum(sampled_differences) / sample_count
    large_change_ratio = sampled_large_changes / sample_count
    if average_difference > 28 or large_change_ratio > 0.30:
        raise MarkedScreenshotError(
            "Ảnh gửi lại không phải bản chỉnh sửa của ảnh Jarvis vừa gửi."
        )
    noise_index = min(sample_count - 1, int(sample_count * 0.99))
    noise_level = sampled_differences[noise_index]
    threshold = max(18, min(90, round(noise_level + 14)))

    point_strength = {}
    for pixel_index in range(width * height):
        offset = pixel_index * 3
        channel_differences = [
            abs(marked[offset + channel] - original[offset + channel])
            for channel in range(3)
        ]
        strength = max(channel_differences)
        if strength >= threshold and sum(channel_differences) >= threshold * 1.35:
            point_strength[pixel_index] = strength
            if len(point_strength) > 100_000:
                raise MarkedScreenshotError(
                    "Ảnh thay đổi quá nhiều; hãy chỉ thêm một dấu chấm nhỏ."
                )

    points = set(point_strength)
    components = []
    while points:
        seed = points.pop()
        stack = [seed]
        members = []
        while stack:
            index = stack.pop()
            members.append(index)
            x, y = index % width, index // width
            for next_y in range(max(0, y - 1), min(height, y + 2)):
                row = next_y * width
                for next_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbour = row + next_x
                    if neighbour in points:
                        points.remove(neighbour)
                        stack.append(neighbour)
        xs = [index % width for index in members]
        ys = [index // width for index in members]
        box_width = max(xs) - min(xs) + 1
        box_height = max(ys) - min(ys) + 1
        strengths = [point_strength[index] for index in members]
        peak = max(strengths)
        score = sum(strengths)
        if (box_width <= 300 and box_height <= 300 and len(members) <= 20_000
                and (len(members) >= 2 or peak >= max(80, threshold * 2))
                and score >= max(90, threshold * 1.8)):
            weight = max(1, score)
            components.append({
                "area": len(members), "score": score, "peak": peak,
                "left": min(xs), "right": max(xs),
                "top": min(ys), "bottom": max(ys),
                "weighted_x": sum(
                    (index % width) * point_strength[index] for index in members
                ),
                "weighted_y": sum(
                    (index // width) * point_strength[index] for index in members
                ),
                "weight": weight,
            })

    # Anti-aliasing or a hollow/cross-shaped marker may produce several small
    # islands. Merge islands within 14 pixels before deciding there are two
    # genuinely separate marks.
    merged = []
    for component in sorted(components, key=lambda item: item["score"], reverse=True):
        match = None
        for candidate in merged:
            horizontal_gap = max(
                0, candidate["left"] - component["right"] - 1,
                component["left"] - candidate["right"] - 1,
            )
            vertical_gap = max(
                0, candidate["top"] - component["bottom"] - 1,
                component["top"] - candidate["bottom"] - 1,
            )
            if horizontal_gap <= 14 and vertical_gap <= 14:
                match = candidate
                break
        if match is None:
            merged.append(dict(component))
            continue
        for key in ("area", "score", "weighted_x", "weighted_y", "weight"):
            match[key] += component[key]
        match["peak"] = max(match["peak"], component["peak"])
        match["left"] = min(match["left"], component["left"])
        match["right"] = max(match["right"], component["right"])
        match["top"] = min(match["top"], component["top"])
        match["bottom"] = max(match["bottom"], component["bottom"])

    if not merged:
        raise MarkedScreenshotError(
            "Ảnh gửi lại chưa có dấu chấm mới. Hãy dùng màu tương phản với nền."
        )
    merged.sort(key=lambda item: item["score"], reverse=True)
    if (len(merged) > 1 and merged[0]["area"] >= 12 and merged[1]["area"] >= 12
            and merged[1]["score"] >= merged[0]["score"] * 0.72):
        raise MarkedScreenshotError(
            "Phát hiện hai vùng đánh dấu rõ ràng; hãy chỉ đánh dấu một vị trí."
        )
    selected = merged[0]
    center_x = selected["weighted_x"] / selected["weight"]
    center_y = selected["weighted_y"] / selected["weight"]
    return round(center_x), round(center_y)


def detect_added_red_marker(original_path, marked_path):
    original_width, original_height, original = _decode_rgb(Path(original_path))
    marked_width, marked_height, marked = _decode_rgb(Path(marked_path))
    if (marked_width, marked_height) != (original_width, original_height):
        raise MarkedScreenshotError(
            f"Ảnh phải giữ nguyên {original_width}×{original_height} pixel."
        )
    x, y = detect_added_red_marker_rgb(
        original, marked, original_width, original_height
    )
    return {"x": x, "y": y, "width": original_width, "height": original_height}
