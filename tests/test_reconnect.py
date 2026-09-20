"""Cancellation recovery with a synthetic owned helper; no MIDI devices."""
import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, patch

from mainstage_mcp.server import Bridge, ToolError
from test_server import FAKE


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_timeout_only_invalidates_when_lock_owned(self):
        bridge = Bridge(['unused'], .01)
        bridge.transaction = {'owner': 'reconnect'}
        bridge.stale = False
        bridge.reason = 'owner snapshot in progress'
        bridge.responsive_clock = 123.0
        bridge.responsive_at = 456.0
        before = (bridge.transaction, bridge.stale, bridge.reason,
                  bridge.responsive_clock, bridge.responsive_at)

        await bridge.lock.acquire()
        try:
            with patch.object(bridge, 'send', new_callable=AsyncMock) as send:
                with self.assertRaises(ToolError):
                    await bridge.refresh()
                self.assertEqual((bridge.transaction, bridge.stale, bridge.reason,
                                  bridge.responsive_clock, bridge.responsive_at), before)
                send.assert_not_awaited()
        finally:
            bridge.lock.release()

        bridge.transaction = {'owner': 'refresh'}
        bridge.stale = False
        bridge.reason = None
        bridge.responsive_clock = 789.0
        async def blocked_send(command):
            await asyncio.Future()
        with patch.object(bridge, 'send', new_callable=AsyncMock,
                          side_effect=blocked_send) as send:
            with self.assertRaises(ToolError):
                await bridge.refresh()
            send.assert_awaited_once_with('refresh')
        self.assertIsNone(bridge.transaction)
        self.assertTrue(bridge.stale)
        self.assertEqual(bridge.reason, 'refresh failed or timed out')
        self.assertIsNone(bridge.responsive_clock)

    async def test_cancellation_closes_helper_and_preserves_stale_snapshot(self):
        helpers = {
            'close': FAKE,
            'ready': FAKE.replace(
                "print(json.dumps(dict(kind='transport',connected=True,route=route)),flush=True)",
                "emit('hello','MainStage',2,'fake-session','selection,patch_list,raw_midi_cc')"),
            'refresh': FAKE.split("        emit('snapshot_begin'")[0],
        }
        for phase, helper in helpers.items():
            with self.subTest(phase=phase):
                bridge = Bridge([sys.executable, '-u', '-c', FAKE], 2)
                await bridge.start()
                task = None
                real_close, real_accept = bridge.close, bridge.accept
                try:
                    initial = await bridge.refresh()
                    old_process, route, counter = bridge.process, bridge.route, bridge.counter
                    bridge.command = [sys.executable, '-u', '-c', helper]
                    reached = asyncio.Event()

                    def accept(event):
                        real_accept(event)
                        if phase != 'close' and event.get('kind') == 'hello':
                            reached.set()

                    async def close(reason='bridge stopped'):
                        if phase == 'close' and reason == 'explicit reconnect pending':
                            reached.set()
                            await asyncio.Future()  # Hold the initial teardown until cancellation.
                        await real_close(reason)

                    with patch.object(bridge, 'accept', side_effect=accept), \
                            patch.object(bridge, 'close', side_effect=close):
                        task = asyncio.create_task(bridge.reconnect())
                        await asyncio.wait_for(reached.wait(), 2)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

                    state = bridge.snapshot()
                    self.assertFalse(state.bridge_running)
                    self.assertFalse(state.transport_connected)
                    self.assertFalse(state.host_responsive)
                    self.assertTrue(state.stale)
                    self.assertEqual(state.reason, 'explicit reconnect cancelled')
                    self.assertEqual(state.session, initial.session)
                    self.assertEqual(state.selection, initial.selection)
                    self.assertEqual(state.items, initial.items)
                    self.assertEqual(bridge.route, route)
                    self.assertEqual(bridge.counter, counter + (phase == 'refresh'))
                    self.assertIsNotNone(old_process.returncode)
                    self.assertTrue(bridge.reader.done())
                    self.assertFalse(bridge.pending)
                    self.assertFalse(bridge.responses)
                finally:
                    if task is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    await real_close()


if __name__ == '__main__':
    unittest.main()
