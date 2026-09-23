"""Synthetic subprocess coverage for the opt-in live recovery check."""
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import live_recovery_check
from test_server import FAKE

from mainstage_mcp.server import Bridge


class LiveRecoveryCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_default_and_owned_helper_crash_recovery(self):
        for crash, expected_lines in ((False, 1), (True, 3)):
            with self.subTest(crash=crash):
                bridge = Bridge([sys.executable, '-u', '-c', FAKE], .5)
                args = SimpleNamespace(bridge='unused', input='Input', output='Output',
                                       concert='Fake concert', crash_helper=crash)
                with patch.object(live_recovery_check, 'Bridge', return_value=bridge), \
                        patch('builtins.print') as output:
                    await live_recovery_check.check(args)

                states = [json.loads(call.args[0]) for call in output.call_args_list]
                self.assertEqual(len(states), expected_lines)
                self.assertEqual(bridge.counter, 1 + crash)
                self.assertIsNotNone(bridge.process.returncode)
                if crash:
                    initial, crashed, recovered = (states[0]['initial'], states[1]['crashed'],
                                                   states[2]['recovered'])
                    self.assertEqual(crashed['selection'], initial['selection'])
                    self.assertEqual(crashed['session'], initial['session'])
                    self.assertTrue(crashed['stale'])
                    self.assertFalse(crashed['bridge_running'])
                    self.assertFalse(crashed['transport_connected'])
                    self.assertNotEqual(recovered['session'], initial['session'])
                    self.assertFalse(recovered['stale'])

    async def test_concert_guard_stops_before_crash(self):
        bridge = Bridge([sys.executable, '-u', '-c', FAKE], .5)
        args = SimpleNamespace(bridge='unused', input='Input', output='Output',
                               concert='Wrong concert', crash_helper=True)
        with patch.object(live_recovery_check, 'Bridge', return_value=bridge), \
                patch('builtins.print'):
            with self.assertRaisesRegex(RuntimeError, 'Exact disposable concert'):
                await live_recovery_check.check(args)
        self.assertEqual(bridge.counter, 1)
        self.assertIsNotNone(bridge.process.returncode)


if __name__ == '__main__':
    unittest.main()
