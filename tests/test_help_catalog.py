import unittest

from jarvis_core.help_catalog import (
    HELP_CATEGORIES,
    format_help_category,
    format_help_overview,
    format_recent_updates,
    format_category_choices,
    resolve_help_category,
)


class HelpCatalogTests(unittest.TestCase):
    def test_resolves_vietnamese_categories_without_accents(self):
        self.assertEqual(resolve_help_category("bộ nhớ"), "memory")
        self.assertEqual(resolve_help_category("ổ đĩa"), "files")
        self.assertEqual(resolve_help_category("âm lượng"), "audio")

    def test_overview_lists_every_category(self):
        overview = format_help_overview()
        for category in HELP_CATEGORIES.values():
            self.assertIn(category["title"], overview)

    def test_memory_help_contains_actionable_commands(self):
        message = format_help_category("memory")
        self.assertIn("ghi nhớ", message)
        self.assertIn("nhớ lại", message)
        self.assertIn("quên mục", message)

    def test_recent_updates_include_actionable_examples(self):
        message = format_recent_updates()
        self.assertIn("Sleep theo giờ", message)
        self.assertIn("sleep sâu lúc 23:30", message)
        self.assertIn("Bảo mật Discord", message)

    def test_zalo_and_reminders_are_discoverable_everywhere(self):
        self.assertIn("zalo", HELP_CATEGORIES)
        zalo = "\n".join(command for _label, command in HELP_CATEGORIES["zalo"]["commands"])
        power = "\n".join(command for _label, command in HELP_CATEGORIES["power"]["commands"])
        self.assertIn("tóm tắt zalo ngày", zalo)
        self.assertIn("gửi tin nhắn cho Minh", zalo)
        self.assertIn("sau 2h nữa", zalo)
        self.assertIn("nhắc tôi sau", power)
        web = "\n".join(command for _label, command in HELP_CATEGORIES["web"]["commands"])
        self.assertIn("youtube đang phát gì", web)
        self.assertIn("Zalo công việc", format_category_choices())


if __name__ == "__main__":
    unittest.main()
