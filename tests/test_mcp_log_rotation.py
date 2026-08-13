import tempfile
import unittest
import os
from pathlib import Path

import jarvis


class MCPLogRotationTests(unittest.TestCase):
    def test_mcp_disables_crux_network_lookup(self):
        server = jarvis.create_mcp_server()

        self.assertIn("--no-performance-crux", server.args)

    def test_mcp_log_filter_drops_only_known_upstream_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "mcp_errors.log"
            errlog = jarvis.MCPErrorLogFilter(log_path)
            os.write(
                errlog.fileno(),
                (
                    "No handler registered for issue code PerformanceIssue\n"
                    "A real MCP error\n"
                ).encode(),
            )
            errlog.close()

            self.assertEqual(
                log_path.read_text(encoding="utf-8"),
                "A real MCP error\n",
            )

    def test_mcp_log_filter_flushes_all_queued_stderr_before_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "mcp_errors.log"
            errlog = jarvis.MCPErrorLogFilter(log_path)
            expected = b"real warning\n" * 2000
            os.write(errlog.fileno(), expected)

            errlog.close()

            self.assertEqual(log_path.read_bytes(), expected)

    def test_oversized_log_is_rotated_before_new_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "mcp_errors.log"
            log_path.write_text("old warning\n" * 20, encoding="utf-8")

            jarvis.rotate_mcp_error_log(log_path, max_bytes=32, backup_count=2)

            self.assertFalse(log_path.exists())
            self.assertEqual(
                (Path(str(log_path) + ".1")).read_text(encoding="utf-8"),
                "old warning\n" * 20,
            )


if __name__ == "__main__":
    unittest.main()
