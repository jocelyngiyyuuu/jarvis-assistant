import unittest

from jarvis_core.marked_screenshot import (
    MarkedScreenshotError,
    detect_added_red_marker_rgb,
)


class MarkedScreenshotTests(unittest.TestCase):
    @staticmethod
    def _image(width, height, color=(240, 240, 240)):
        return bytearray(bytes(color) * width * height)

    @staticmethod
    def _dot(image, width, center_x, center_y, radius=3, color=(255, 0, 0)):
        for y in range(center_y - radius, center_y + radius + 1):
            for x in range(center_x - radius, center_x + radius + 1):
                offset = (y * width + x) * 3
                image[offset:offset + 3] = bytes(color)

    def test_finds_one_new_red_dot(self):
        width, height = 80, 50
        original = self._image(width, height)
        marked = bytearray(original)
        self._dot(marked, width, 31, 22)
        self.assertEqual(
            detect_added_red_marker_rgb(original, marked, width, height),
            (31, 22),
        )

    def test_ignores_red_already_present_in_original(self):
        width, height = 80, 50
        original = self._image(width, height)
        self._dot(original, width, 10, 10)
        marked = bytearray(original)
        self._dot(marked, width, 50, 30)
        self.assertEqual(
            detect_added_red_marker_rgb(original, marked, width, height),
            (50, 30),
        )

    def test_finds_blue_and_black_markers(self):
        width, height = 80, 50
        for color in ((0, 80, 255), (0, 0, 0), (255, 220, 0)):
            with self.subTest(color=color):
                original = self._image(width, height)
                marked = bytearray(original)
                self._dot(marked, width, 37, 19, radius=2, color=color)
                self.assertEqual(
                    detect_added_red_marker_rgb(original, marked, width, height),
                    (37, 19),
                )

    def test_finds_one_pixel_high_contrast_marker(self):
        width, height = 80, 50
        original = self._image(width, height)
        marked = bytearray(original)
        offset = (24 * width + 42) * 3
        marked[offset:offset + 3] = bytes((0, 0, 0))
        self.assertEqual(
            detect_added_red_marker_rgb(original, marked, width, height),
            (42, 24),
        )

    def test_tolerates_small_reencoding_noise(self):
        width, height = 80, 50
        original = self._image(width, height, color=(120, 120, 120))
        marked = bytearray(original)
        for offset in range(0, len(marked), 3):
            noise = ((offset // 3) % 7) - 3
            for channel in range(3):
                marked[offset + channel] = 120 + noise
        self._dot(marked, width, 55, 31, radius=2, color=(0, 220, 255))
        self.assertEqual(
            detect_added_red_marker_rgb(original, marked, width, height),
            (55, 31),
        )

    def test_rejects_multiple_markers(self):
        width, height = 80, 50
        original = self._image(width, height)
        marked = bytearray(original)
        self._dot(marked, width, 20, 20)
        self._dot(marked, width, 60, 30)
        with self.assertRaises(MarkedScreenshotError):
            detect_added_red_marker_rgb(original, marked, width, height)

    def test_rejects_unmodified_original(self):
        width, height = 80, 50
        original = self._image(width, height)
        with self.assertRaisesRegex(MarkedScreenshotError, "chưa có dấu chấm"):
            detect_added_red_marker_rgb(
                original, bytearray(original), width, height
            )

    def test_rejects_unrelated_same_size_image_with_red_dot(self):
        width, height = 80, 50
        original = self._image(width, height, color=(240, 240, 240))
        marked = self._image(width, height, color=(20, 20, 20))
        self._dot(marked, width, 40, 25)
        with self.assertRaisesRegex(MarkedScreenshotError, "không phải bản chỉnh sửa"):
            detect_added_red_marker_rgb(original, marked, width, height)


if __name__ == "__main__":
    unittest.main()
