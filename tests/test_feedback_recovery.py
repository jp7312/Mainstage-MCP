"""Synthetic recovery checks; no live MainStage or CoreMIDI evidence."""
import asyncio
import sys
import unittest

from mcp.server.mcpserver.exceptions import ToolError
from mainstage_mcp.server import Bridge
from test_server import FAKE, PARAM_FAKE


class FeedbackRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_goodbye_reinitialize_keeps_complete_snapshot_and_rejects_held_context(self):
        fake = FAKE.replace('revision, program = 1, 0', 'revision, program, refreshes = 1, 0, 0')
        fake = fake.replace("    if c['command']=='refresh':",
                            "    if c['command']=='refresh':\n        refreshes += 1")
        fake = fake.replace("'fake-session'", "('initial' if refreshes == 1 else 'reinitialized')")
        bridge = Bridge([sys.executable, '-u', '-c', fake], .5)
        await bridge.start()
        try:
            initial = await bridge.refresh()
            bridge.accept(dict(kind='snapshot_begin', fields=['initial', '2', '']))
            bridge.accept(dict(kind='selection', fields=['1', '0', '1', 'Partial', 'Set', 'Partial']))
            bridge.accept(dict(kind='goodbye', fields=['initial']))
            stopped = bridge.snapshot()
            self.assertTrue(stopped.stale)
            self.assertFalse(stopped.host_responsive)
            self.assertFalse(stopped.profile_seen)
            self.assertEqual(stopped.capabilities, [])
            self.assertEqual(stopped.selection, initial.selection)
            self.assertEqual(stopped.session, initial.session)
            self.assertIsNone(bridge.transaction)

            recovered = await bridge.refresh()
            self.assertFalse(recovered.stale)
            self.assertNotEqual(recovered.session, initial.session)
            bridge.accept(dict(kind='goodbye', fields=['initial']))
            self.assertFalse(bridge.snapshot().stale, 'An old session goodbye cannot finalize the new profile')
            with self.assertRaisesRegex(ToolError, 'No MIDI mutation sent: context changed'):
                await bridge.mutate('cc', initial.session, initial.revision, control=7, value=10, channel=1)
            self.assertEqual(bridge.counter, 3, 'Only initial, recovery and preflight refreshes were sent')
        finally:
            await bridge.close()

    async def test_unrelated_topology_disconnect_recovers_only_on_the_pinned_route(self):
        bridge = Bridge([sys.executable, '-u', '-c', FAKE], .5)
        await bridge.start()
        try:
            initial = await bridge.refresh()
            route = bridge.route
            bridge.accept(dict(kind='transport', connected=False, reason='CoreMIDI topology changed'))
            disconnected = bridge.snapshot()
            self.assertTrue(disconnected.stale)
            self.assertFalse(disconnected.transport_connected)
            self.assertEqual(disconnected.selection, initial.selection)
            self.assertEqual(disconnected.observed_at, initial.observed_at)
            with self.assertRaisesRegex(ToolError, 'No MIDI mutation sent'):
                await bridge.mutate('pc', initial.session, initial.revision, program=1, channel=1)
            self.assertEqual(bridge.counter, 1)

            recovered = await bridge.reconnect()
            self.assertFalse(recovered.stale)
            self.assertEqual(bridge.route, route)
            self.assertNotEqual(recovered.session, initial.session)
            self.assertEqual(bridge.counter, 2, 'Reconnect sends a refresh without replaying the failed mutation')
        finally:
            await bridge.close()

    async def test_expired_snapshot_and_callback_replies_across_helper_reconnect(self):
        bridge = Bridge([sys.executable, '-u', '-c', PARAM_FAKE], .5)
        def emit(kind, *fields):
            bridge.accept(dict(kind=kind, fields=list(map(str, fields))))
        def snapshot(request, revision, program, label):
            emit('snapshot_begin', 'fake-session', revision, request)
            emit('selection', program, 0, program, 'Fake concert', 'Set', label)
            emit('item', 1, 0, program, label)
            emit('snapshot_end', 'fake-session', revision, request, 1)

        await bridge.start()
        recovery = None
        try:
            initial = await bridge.refresh()
            expired_request = f'{bridge.request_prefix}_{bridge.counter}'
            # The replacement helper acknowledges commands; this test controls feedback ordering.
            bridge.command = [sys.executable, '-u', '-c', FAKE.split("    if c['command']=='pc':")[0]]
            recovery = asyncio.create_task(bridge.reconnect())
            async with asyncio.timeout(2):
                while not bridge.responses:
                    await asyncio.sleep(.001)
            current_request = next(iter(bridge.responses))
            self.assertNotEqual(current_request, expired_request)
            emit('hello', 'MainStage', 2, 'fake-session',
                 'selection,patch_list,raw_midi_cc,mapped_parameter_1')
            snapshot(expired_request, 2, 1, 'Delayed')
            waiting = bridge.snapshot()
            self.assertTrue(waiting.stale)
            self.assertFalse(waiting.host_responsive)
            self.assertEqual(waiting.session, initial.session)
            self.assertEqual(waiting.selection, initial.selection)
            self.assertEqual(waiting.observed_at, initial.observed_at)
            self.assertFalse(recovery.done())

            # Refresh replay is the same old callback, even on the replacement helper.
            emit('parameter', 'fake-session', 1, 1, 'mapped_parameter_1', 40,
                 0, 127, 'midi_7bit', 'Cutoff', '1.2 kHz', 'screen_control_feedback')
            snapshot(current_request, 3, 2, 'Current')
            recovered = await asyncio.wait_for(recovery, 1)
            self.assertFalse(recovered.stale)
            self.assertNotEqual(recovered.session, initial.session)
            self.assertEqual(recovered.mapped_parameter.observedAt, initial.mapped_parameter.observedAt)
            self.assertTrue(recovered.mapped_parameter.stale)
            self.assertIn('connection session changed', recovered.mapped_parameter.reason)

            snapshot(expired_request, 2, 1, 'Delayed')
            self.assertEqual(bridge.snapshot(), recovered, 'A late tagged snapshot cannot replace or stale recovered state')
            snapshot('', 4, 3, 'Unsolicited')
            self.assertEqual(bridge.snapshot().selection.patch, 'Unsolicited')
            complete = bridge.complete
            for removed in (False, True):
                pending = bridge.responses['cancelled'] = asyncio.get_running_loop().create_future()
                emit('snapshot_begin', 'fake-session', 5, 'cancelled')
                emit('selection', 4, 0, 4, 'Fake concert', 'Set', 'Expired midway')
                pending.cancel()
                if removed:
                    bridge.responses.pop('cancelled')
                emit('snapshot_end', 'fake-session', 5, 'cancelled', 0)
                self.assertEqual(bridge.complete, complete, 'A request expiring mid-snapshot cannot commit')
                self.assertTrue(bridge.snapshot().stale)
                self.assertIsNone(bridge.transaction)
                bridge.responses.pop('cancelled', None)
            self.assertEqual(bridge.counter, 2, 'Recovery sends only the initial and replacement refreshes')
            self.assertFalse(bridge.pending)
            self.assertFalse(bridge.responses)
        finally:
            if recovery and not recovery.done():
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
            await bridge.close()


if __name__ == '__main__':
    unittest.main()
