"""Smoke-client guard checks use a fake client, never MainStage or MIDI."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import live_check


class LiveCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_is_opt_in_and_guards_optional_programs(self):
        initial = dict(stale=False, host_responsive=True, selection=dict(concert='Disposable'),
                       session='before', revision=1)
        recovered = dict(initial, session='after')
        refresh, reconnect = call('mainstage_refresh'), call('mainstage_reconnect')
        cases = [('default', False, [], [initial], [refresh], None),
                 ('reconnect', True, [], [initial, recovered], [refresh, reconnect], None)]
        for enabled in (False, True):
            state = dict(recovered if enabled else initial, revision=2)
            responses = [initial, recovered] if enabled else [initial]
            calls = [refresh, reconnect] if enabled else [refresh]
            cases.append((f'program after reconnect={enabled}', enabled, [4],
                          responses + [state, dict(sent=True, observed=True)],
                          calls + [refresh, call('mainstage_select_program', dict(
                              program=4, expected_session=state['session'], expected_revision=2))], None))
        cases.append(('unobserved program is never retried', True, [4, 5],
                      [initial, recovered, recovered, dict(sent=True, observed=False)],
                      [refresh, reconnect, refresh, call('mainstage_select_program', dict(
                          program=4, expected_session='after', expected_revision=1))], 'without retry'))
        for updates, error in ((dict(stale=True), 'fresh'),
                               (dict(host_responsive=False), 'responsive'),
                               (dict(selection=dict(concert='Other')), 'Concert'),
                               (dict(session=''), 'session')):
            cases.append((f'invalid initial {updates}', True, [4], [dict(initial, **updates)],
                          [refresh], error))
            cases.append((f'invalid recovery {updates}', True, [4],
                          [initial, dict(recovered, **updates)], [refresh, reconnect], error))
        cases.append(('unchanged session', True, [4], [initial, initial],
                      [refresh, reconnect], 'session'))
        for name, enabled, programs, responses, expected_calls, error in cases:
            with self.subTest(name=name), patch.object(live_check, 'Client') as factory, \
                    patch('builtins.print'):
                client = factory.return_value.__aenter__.return_value
                client.protocol_version = 'test'
                client.call_tool = AsyncMock(side_effect=[
                    SimpleNamespace(is_error=False, structured_content=value) for value in responses])
                args = SimpleNamespace(bridge='build/bridge', input='in', output='out',
                                       concert='Disposable', reconnect=enabled, program=programs)
                if error:
                    with self.assertRaisesRegex(RuntimeError, error):
                        await live_check.check(args)
                else:
                    await live_check.check(args)
                self.assertEqual(client.call_tool.call_args_list, expected_calls)


if __name__ == '__main__':
    unittest.main()
