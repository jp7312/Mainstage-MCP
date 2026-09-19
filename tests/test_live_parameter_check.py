"""The mapped-parameter live probe is guarded and never retries a write."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import live_parameter_check


class LiveParameterCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_gate_and_single_write(self):
        baseline = dict(id='mapped_parameter_1', session='session', selectionRevision=7,
                        sequence=3, rawValue=40, source='screen_control_feedback', stale=False)
        state = dict(stale=False, host_responsive=True, selection=dict(concert='Disposable'),
                     capabilities=['mapped_parameter_1'], session='session', revision=7,
                     mapped_parameter=baseline)
        args = SimpleNamespace(bridge='build/bridge', input='in', output='out',
                               concert='Disposable', value=90)
        setter = call('mainstage_set_mapped_parameter_1', dict(
            value=90, expected_session='session', expected_revision=7))
        feedback = dict(baseline, sequence=4, rawValue=90)
        cases = [
            ('missing baseline', dict(state, mapped_parameter=None), [], True),
            ('stale baseline', dict(state, mapped_parameter=dict(baseline, stale=True)), [], True),
            ('same value', state, [], True, 40),
            ('success', state, [dict(sent=True, observed=True,
                                    state=dict(mapped_parameter=feedback))], False),
            ('timeout', state, [dict(sent=True, observed=False, timed_out=True,
                                    state=dict(mapped_parameter=baseline))], True),
        ]
        for case in cases:
            name, initial, results, fails, *value = case
            with self.subTest(name=name), patch.object(live_parameter_check, 'Client') as factory, \
                    patch('builtins.print'):
                client = factory.return_value.__aenter__.return_value
                client.call_tool = AsyncMock(side_effect=[
                    SimpleNamespace(is_error=False, structured_content=item)
                    for item in [initial, *results]])
                args.value = value[0] if value else 90
                if fails:
                    with self.assertRaises(RuntimeError):
                        await live_parameter_check.check(args)
                else:
                    await live_parameter_check.check(args)
                expected = [call('mainstage_refresh')] + ([setter] if results else [])
                self.assertEqual(client.call_tool.call_args_list, expected)


if __name__ == '__main__':
    unittest.main()
