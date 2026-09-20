"""Dispatch tests for the top-level mainstage-mcp CLI."""
import unittest
from unittest.mock import patch

from mainstage_mcp import __main__ as cli


class DispatchTest(unittest.TestCase):
    def test_help(self):
        self.assertEqual(cli.main([]), 0)
        self.assertEqual(cli.main(["--help"]), 0)

    def test_unknown_command(self):
        self.assertEqual(cli.main(["frobnicate"]), 2)

    def test_version(self):
        with patch("builtins.print") as shown:
            self.assertEqual(cli.main(["--version"]), 0)
            shown.assert_called_once()

    def test_serve_returns_int(self):
        with patch("mainstage_mcp.server.main", return_value=None) as serve:
            self.assertEqual(cli.main(["serve", "--bridge", "x"]), 0)
            serve.assert_called_once_with(["--bridge", "x"])

    def test_serve_bad_timeout_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            cli.main(["serve", "--bridge", "x", "--timeout", "0"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
