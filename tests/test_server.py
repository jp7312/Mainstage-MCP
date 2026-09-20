"""Offline regressions: simulated native bridge, real official SDK client. No MainStage."""
import asyncio
import io
import json
import os
from contextlib import redirect_stderr
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mainstage_mcp.server import Bridge, create_server, main
from mcp.server.mcpserver.exceptions import ToolError

FAKE = r'''
import json,sys
revision, program = 1, 0
route = [['destination','Input',101,201,301,'com.apple.AppleMIDIIACDriver'],['source','Output',102,202,301,'com.apple.AppleMIDIIACDriver']]
def emit(kind, *fields):
    print(json.dumps(dict(kind=kind, fields=list(map(str,fields)))), flush=True)
print(json.dumps(dict(kind='transport',connected=True,route=route)),flush=True)
for line in sys.stdin:
    c=json.loads(line)
    print(json.dumps(dict(kind='command_result',id=c['id'],ok=True,status=0)),flush=True)
    if c['command']=='pc':
        program=c['program']; revision+=1
    if c['command']=='refresh':
        emit('hello','MainStage',2,'fake-session','selection,patch_list,raw_midi_cc')
        emit('snapshot_begin','fake-session',revision,c['id'])
        emit('selection',program,0,0,'Fake concert','Set','Żółć 🎹')
        emit('item',1,0,0,'Żółć 🎹')
        emit('snapshot_end','fake-session',revision,c['id'],1)
'''

PARAM_FAKE = r'''
import json,sys
revision, sequence, raw = 1, 1, 40
route = [['destination','Input',101,201,301,'com.apple.AppleMIDIIACDriver'],['source','Output',102,202,301,'com.apple.AppleMIDIIACDriver']]
def emit(kind, *fields):
    print(json.dumps(dict(kind=kind, fields=list(map(str,fields)))), flush=True)
print(json.dumps(dict(kind='transport',connected=True,route=route)),flush=True)
for line in sys.stdin:
    c=json.loads(line)
    print(json.dumps(dict(kind='command_result',id=c['id'],ok=True,status=0)),flush=True)
    if c['command']=='cc' and c['control']==90:
        raw=c['value']; sequence+=1
        emit('parameter','fake-session',revision,sequence,'mapped_parameter_1',raw,0,127,'midi_7bit','Cutoff','1.2 kHz','screen_control_feedback')
    if c['command']=='refresh':
        emit('hello','MainStage',2,'fake-session','selection,patch_list,raw_midi_cc,mapped_parameter_1')
        emit('parameter','fake-session',revision,sequence,'mapped_parameter_1',raw,0,127,'midi_7bit','Cutoff','1.2 kHz','screen_control_feedback')
        emit('snapshot_begin','fake-session',revision,c['id'])
        emit('selection',0,0,0,'Fake concert','Set','Patch')
        emit('item',1,0,0,'Patch')
        emit('snapshot_end','fake-session',revision,c['id'],1)
'''


def value(result):
    return result.structured_content or json.loads(result.content[0].text)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_client_and_mutations(self):
        bridge = Bridge([sys.executable, '-u', '-c', FAKE], .5)
        async with Client(create_server(bridge)) as client:
            listed = await client.list_tools()
            self.assertEqual(len(listed.tools), 10)
            state = value(await client.call_tool('mainstage_refresh'))
            self.assertFalse(state['stale'])
            self.assertTrue(state['host_responsive'])
            args = dict(expected_session=state['session'],expected_revision=state['revision'])
            bad = await client.call_tool('mainstage_send_cc', dict(args,control=True,value=0))
            self.assertTrue(bad.is_error)
            bad = await client.call_tool('mainstage_send_cc',dict(args,control=128,value=0))
            self.assertTrue(bad.is_error)
            selected = value(await client.call_tool('mainstage_select_program', dict(args,program=4)))
            self.assertTrue(selected['sent']); self.assertTrue(selected['observed'])
            changed = await client.call_tool('mainstage_send_cc',dict(args,control=7,value=10))
            self.assertTrue(changed.is_error)
            self.assertIn('No MIDI mutation sent: context changed',changed.content[0].text)
            state = value(await client.call_tool('mainstage_refresh'))
            args['expected_revision'] = state['revision']
            cc = value(await client.call_tool('mainstage_send_cc',dict(args,control=7,value=10)))
            self.assertTrue(cc['sent']); self.assertFalse(cc['observed'])
            self.assertTrue(value(await client.call_tool('mainstage_get_state'))['stale'])
            await asyncio.sleep(.55)
            self.assertTrue(value(await client.call_tool('mainstage_get_state'))['stale'])
        self.assertIsNotNone(bridge.process.returncode)

    async def test_atomic_session_and_malformed_feedback(self):
        b = Bridge(['unused'])
        def emit(kind, *fields): b.accept(dict(kind=kind,fields=list(map(str,fields))))
        emit('hello','MainStage',2,'a','selection,patch_list,raw_midi_cc')
        emit('snapshot_begin','a',1,'')
        emit('selection',0,0,0,'A','Set','Old')
        emit('item',1,0,0,'Old')
        emit('snapshot_end','a',1,'',1)
        emit('hello','MainStage',2,'b','selection,patch_list,raw_midi_cc')
        emit('snapshot_begin','b',1,'')
        emit('selection',2,0,0,'B','Set','New')
        state = b.snapshot()
        self.assertEqual(state.selection.concert,'A'); self.assertTrue(state.stale)
        with self.assertRaises(ValueError): emit('snapshot_end','b',1,'wrong',0)
        self.assertEqual(b.snapshot().items[0].label,'Old')
        emit('snapshot_begin','b',1,'')
        emit('selection',2,0,0,'B','Set','New')
        emit('snapshot_end','b',1,'',0)
        self.assertEqual(b.snapshot().selection.concert,'B')
        emit('error','','invalid_snapshot','invalid callback')
        self.assertTrue(b.snapshot().stale)
        self.assertEqual(b.snapshot().selection.concert,'B')
        with self.assertRaises(ValueError): emit('snapshot_begin','a',2,'')
        with self.assertRaises(ValueError): emit('snapshot_begin','b',0,'')
        self.assertTrue(b.snapshot().stale)

    async def test_deadline_and_disconnect(self):
        # Transport ACK alone must never count as a responsive MainStage profile.
        fake = FAKE.split("    if c['command']=='pc':")[0]
        b = Bridge([sys.executable,'-u','-c',fake], .1)
        await b.start()
        try:
            with self.assertRaises(ToolError): await b.refresh()
            self.assertFalse(b.snapshot().host_responsive)
            self.assertTrue(b.snapshot().stale)
            self.assertFalse(b.pending); self.assertFalse(b.responses)
        finally: await b.close()
        self.assertFalse(b.snapshot().bridge_running)

    async def test_mutation_lock_timeout_preserves_active_snapshot(self):
        b = Bridge(['unused'], .01)
        transaction = {'request': 'active'}
        b.transaction = transaction
        b.stale, b.reason = True, 'snapshot incomplete'
        b.responsive_clock, b.responsive_at = 123.0, 456.0
        freshness = (b.stale, b.reason, b.responsive_clock, b.responsive_at)
        sends = 0

        async def send(*args, **values):
            nonlocal sends
            sends += 1

        b.send = send
        await b.lock.acquire()
        try:
            with self.assertRaises(ToolError) as timed_out:
                await asyncio.wait_for(
                    b.mutate('cc', 'session', 1, control=7, value=10, channel=1), .2)
        finally:
            b.lock.release()
        self.assertEqual(str(timed_out.exception), 'No MIDI mutation sent: timeout')
        self.assertEqual(sends, 0)
        self.assertIs(b.transaction, transaction)
        self.assertEqual((b.stale, b.reason, b.responsive_clock, b.responsive_at), freshness)

    async def test_explicit_reconnect_after_helper_crash_invalidates_held_context(self):
        b = Bridge([sys.executable,'-u','-c',FAKE], .5)
        await b.start()
        try:
            initial = await b.refresh()
            first_request_prefix = b.request_prefix
            b.process.kill()
            await asyncio.wait_for(b.reader, 1)
            self.assertTrue(b.snapshot().stale)
            recovered = await b.reconnect()
            self.assertFalse(recovered.stale)
            self.assertNotEqual(recovered.session, initial.session)
            self.assertEqual(b.route[0][2], 101)
            self.assertEqual(b.request_prefix, first_request_prefix)
            with self.assertRaises(ToolError) as held:
                await b.mutate('cc', initial.session, initial.revision, control=7, value=10, channel=1)
            self.assertIn('context changed', str(held.exception))
        finally: await b.close()

    async def test_reconnect_refuses_changed_endpoint_identity_without_commands(self):
        b = Bridge([sys.executable,'-u','-c',FAKE], .5)
        await b.start()
        try:
            initial = await b.refresh()
            b.command = [sys.executable,'-u','-c',FAKE.replace("'Input',101", "'Input',999")]
            with self.assertRaises(ToolError) as refused: await b.reconnect()
            self.assertIn('endpoint identity changed', str(refused.exception))
            state = b.snapshot()
            self.assertTrue(state.stale); self.assertFalse(state.transport_connected)
            self.assertEqual(state.session, initial.session)
        finally: await b.close()

    async def test_request_ids_are_process_unique_and_route_events_are_strict(self):
        first, second = Bridge(['unused']), Bridge(['unused'])
        self.assertNotEqual(first.request_prefix, second.request_prefix)
        for route in (None, [], [['destination','Input',101,201,301,'driver']]):
            with self.assertRaises(ValueError):
                first.accept(dict(kind='transport', connected=True, route=route))

    async def test_sent_but_feedback_missing_and_bounded_reader(self):
        fake = FAKE.replace("if c['command']=='refresh':", "if c['command']=='refresh' and program == 0:")
        b = Bridge([sys.executable,'-u','-c',fake], .15)
        await b.start()
        try:
            initial = await b.refresh()
            result = await b.mutate('pc', initial.session, initial.revision, program=3, channel=1)
            self.assertTrue(result['sent']); self.assertFalse(result['observed'])
            self.assertTrue(b.snapshot().stale)
            self.assertFalse(b.responses); self.assertFalse(b.pending)
        finally: await b.close()
        b = Bridge([sys.executable,'-u','-c',"import sys,time; print('x'*600000,flush=True); time.sleep(5)"], .2)
        await b.start()
        try:
            await asyncio.wait_for(b.reader, 1)
            self.assertTrue(b.snapshot().stale)
            self.assertFalse(b.snapshot().transport_connected)
        finally: await b.close()

    async def test_cancel_during_mutation(self):
        fake=FAKE.replace("    print(json.dumps(dict(kind='command_result'", "    if c['command']=='pc':\n        import time; time.sleep(30)\n    print(json.dumps(dict(kind='command_result'")
        b=Bridge([sys.executable,'-u','-c',fake],1)
        await b.start()
        try:
            s=await b.refresh()
            t=asyncio.create_task(b.mutate('pc',s.session,s.revision,program=5,channel=1))
            async with asyncio.timeout(2):
                while b.counter < 3: await asyncio.sleep(.001)
            await asyncio.sleep(.02)
            t.cancel()
            with self.assertRaises(asyncio.CancelledError): await t
            self.assertFalse(b.pending); self.assertFalse(b.responses)
            self.assertTrue(b.snapshot().stale, 'Cancellation during MIDI send must mark cached snapshot stale')
        finally: await b.close()

    async def test_delayed_selection_polls_without_resending_pc(self):
        fake = FAKE.replace('revision, program = 1, 0',
                            'revision, program = 1, 0\npending, polls, pc_count = None, 0, 0')
        fake = fake.replace("program=c['program']; revision+=1", "pending=c['program']; polls=0; pc_count+=1\n        assert pc_count == 1, 'PC must not be retried'")
        fake = fake.replace("    if c['command']=='refresh':", "    if c['command']=='refresh':\n        if pending is not None:\n            polls+=1\n            if polls>=3:\n                program=pending; pending=None; revision+=1")
        b = Bridge([sys.executable,'-u','-c',fake],1)
        await b.start()
        try:
            initial = await b.refresh()
            result = await b.mutate('pc', initial.session, initial.revision, program=5, channel=1)
            self.assertTrue(result['sent']); self.assertTrue(result['observed'])
            self.assertEqual(b.counter,6)  # Initial + preflight + one PC + three polls.
            self.assertFalse(result['state']['stale'])
        finally: await b.close()

    async def test_unmatched_selection_expires_despite_responsive_host(self):
        fake = FAKE.replace("program=c['program']; revision+=1", 'pass')
        b = Bridge([sys.executable,'-u','-c',fake],.15)
        await b.start()
        try:
            initial = await b.refresh()
            result = await b.mutate('pc', initial.session, initial.revision, program=5, channel=1)
            self.assertTrue(result['sent']); self.assertFalse(result['observed'])
            self.assertTrue(result['timed_out']); self.assertTrue(result['state']['stale'])
        finally: await b.close()

    async def test_concert_change_stops_selection_observation(self):
        fake = FAKE.replace("'Fake concert'", "'Other concert' if program else 'Fake concert'")
        b = Bridge([sys.executable,'-u','-c',fake],1)
        await b.start()
        try:
            initial = await b.refresh()
            result = await b.mutate('pc', initial.session, initial.revision, program=5, channel=1)
            self.assertTrue(result['sent']); self.assertFalse(result['observed'])
            self.assertTrue(result['context_changed']); self.assertTrue(result['state']['stale'])
            self.assertEqual(b.counter,4)
        finally: await b.close()

    async def test_profile_capabilities_are_negotiated_strictly(self):
        b = Bridge(['unused'])
        self.assertEqual(b.snapshot().capabilities, [])
        for caps in (None, 'selection', 'selection,patch_list,raw_midi_cc,unknown',
                     'selection,patch_list,raw_midi_cc,selection'):
            fields = ['MainStage','2','s'] + ([] if caps is None else [caps])
            with self.assertRaises(ValueError): b.accept(dict(kind='hello',fields=fields))
            self.assertEqual(b.snapshot().capabilities, [])
        b.accept(dict(kind='hello',fields=['MainStage','2','s',
                      'selection,patch_list,raw_midi_cc,metronome_toggle']))
        self.assertIn('metronome_toggle', b.snapshot().capabilities)
        b.accept(dict(kind='hello',fields=['MainStage','2','s',
                      'selection,patch_list,raw_midi_cc,mapped_parameter_1']))
        self.assertIn('mapped_parameter_1', b.snapshot().capabilities)

    async def test_parameter_feedback_sequence_freshness_and_observation(self):
        b = Bridge([sys.executable, '-u', '-c', PARAM_FAKE], .3)
        async with Client(create_server(b)) as client:
            state = value(await client.call_tool('mainstage_refresh'))
            parameter = state['mapped_parameter']
            self.assertEqual(parameter['session'], state['session'])
            self.assertNotEqual(parameter['session'], 'fake-session')
            self.assertEqual((parameter['rawValue'], parameter['minimum'], parameter['maximum']), (40, 0, 127))
            self.assertEqual(parameter['rawUnit'], 'midi_7bit')
            self.assertFalse(parameter['stale'])
            observed_at = parameter['observedAt']
            state = value(await client.call_tool('mainstage_refresh'))
            self.assertEqual(state['mapped_parameter']['observedAt'], observed_at, 'refresh replay is not a new observation')
            args = dict(value=90, expected_session=state['session'], expected_revision=state['revision'])
            result = value(await client.call_tool('mainstage_set_mapped_parameter_1', args))
            self.assertTrue(result['sent']); self.assertTrue(result['observed'])
            self.assertEqual(result['state']['mapped_parameter']['sequence'], 2)
            self.assertEqual(result['state']['mapped_parameter']['rawValue'], 90)

        stuck = PARAM_FAKE.replace("raw=c['value']; sequence+=1", "pass")
        b = Bridge([sys.executable, '-u', '-c', stuck], .15)
        async with Client(create_server(b)) as client:
            state = value(await client.call_tool('mainstage_refresh'))
            args = dict(value=90, expected_session=state['session'], expected_revision=state['revision'])
            result = value(await client.call_tool('mainstage_set_mapped_parameter_1', args))
            self.assertTrue(result['sent']); self.assertFalse(result['observed'])
            self.assertTrue(result['timed_out'])

    async def test_parameter_frames_are_strict_and_old_selection_is_stale(self):
        b = Bridge(['unused'])
        def emit(kind, *fields): b.accept(dict(kind=kind, fields=list(map(str, fields))))
        caps = 'selection,patch_list,raw_midi_cc,mapped_parameter_1'
        route = [['destination','Input',101,201,301,'driver'],
                 ['source','Output',102,202,301,'driver']]
        b.accept(dict(kind='transport', connected=True, route=route))
        emit('hello', 'MainStage', 2, 's', caps)
        emit('snapshot_begin', 's', 1, '')
        emit('selection', 0, 0, 0, 'Concert', 'Set', 'One')
        emit('snapshot_end', 's', 1, '', 0)
        fields = ('s', 1, 1, 'mapped_parameter_1', 64, 0, 127, 'midi_7bit', 'Cutoff', '50%', 'screen_control_feedback')
        emit('parameter', *fields)
        first = b.parameter['observedAt']
        emit('parameter', *fields)
        self.assertEqual(b.parameter['observedAt'], first)
        b.connection_prefix = 'replacement'
        emit('parameter', *fields)
        emit('snapshot_begin', 's', 1, '')
        emit('selection', 0, 0, 0, 'Concert', 'Set', 'One')
        emit('snapshot_end', 's', 1, '', 0)
        self.assertIn('connection session changed', b.snapshot().mapped_parameter.reason)
        with self.assertRaisesRegex(ValueError, 'changed without sequence'):
            emit('parameter', *fields[:-4], 'midi_7bit', 'Other', '50%', 'screen_control_feedback')
        with self.assertRaisesRegex(ValueError, 'unsupported parameter mapping'):
            emit('parameter', 's', 1, 2, 'mapped_parameter_1', 64, 0, 127, 'dB', 'Cutoff', '-3 dB', 'screen_control_feedback')
        emit('snapshot_begin', 's', 2, '')
        emit('selection', 1, 0, 1, 'Concert', 'Set', 'Two')
        emit('snapshot_end', 's', 2, '', 0)
        self.assertTrue(b.snapshot().mapped_parameter.stale)
        self.assertIn('selection revision changed', b.snapshot().mapped_parameter.reason)
        emit('hello', 'MainStage', 2, 's', 'selection,patch_list,raw_midi_cc')
        self.assertIn('capability unavailable', b.snapshot().mapped_parameter.reason)

    async def test_named_actions_are_individually_gated_and_send_exactly_one_press_release(self):
        actions = {
            'metronome': ('action_metronome', 80),
            'panic': ('action_panic', 85),
            'master_mute': ('action_master_mute', 86),
            'play_stop': ('action_play_stop', 87),
        }
        with tempfile.TemporaryDirectory() as directory:
            for action, (capability, control) in actions.items():
                with self.subTest(action=action):
                    log = Path(directory) / action
                    fake = FAKE.replace('selection,patch_list,raw_midi_cc',
                                        'selection,patch_list,raw_midi_cc,' + capability)
                    fake = fake.replace('    c=json.loads(line)',
                                        '    c=json.loads(line)\n    with open('+repr(str(log))+
                                        ",'a') as log: log.write(json.dumps(c)+'\\n')")
                    b = Bridge([sys.executable, '-u', '-c', fake], .5)
                    async with Client(create_server(b)) as client:
                        state = value(await client.call_tool('mainstage_refresh'))
                        args = dict(action=action, expected_session=state['session'],
                                    expected_revision=state['revision'])
                        result = value(await client.call_tool('mainstage_trigger_action', args))
                        self.assertEqual(result['action'], action)
                        self.assertEqual(result['experimental'], action != 'metronome')
                        self.assertTrue(result['sent']); self.assertFalse(result['observed'])
                        self.assertEqual(result['messages_confirmed'], 2)
                    commands = [json.loads(line) for line in log.read_text().splitlines()]
                    cc = [{k: c[k] for k in ('control', 'value', 'channel')}
                          for c in commands if c['command'] == 'cc']
                    self.assertEqual(cc, [dict(control=control, value=127, channel=16),
                                          dict(control=control, value=0, channel=16)])

            b = Bridge([sys.executable, '-u', '-c', FAKE], .5)
            async with Client(create_server(b)) as client:
                state = value(await client.call_tool('mainstage_refresh'))
                args = dict(expected_session=state['session'], expected_revision=state['revision'])
                absent = await client.call_tool('mainstage_trigger_action', dict(args, action='panic'))
                self.assertTrue(absent.is_error)
                self.assertIn('does not advertise action_panic', absent.content[0].text)
                unknown = await client.call_tool('mainstage_trigger_action', dict(args, action='record'))
                self.assertTrue(unknown.is_error)

    async def test_metronome_exact_press_release_capability_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            for case in ('absent', 'full', 'partial', 'cancel'):
                log = Path(directory)/case
                fake = FAKE.replace('    c=json.loads(line)',
                                    '    c=json.loads(line)\n    with open('+repr(str(log))+",'a') as log: log.write(json.dumps(c)+'\\n')")
                if case != 'absent':
                    fake = fake.replace('selection,patch_list,raw_midi_cc',
                                        'selection,patch_list,raw_midi_cc,metronome_toggle')
                if case in ('partial', 'cancel'):
                    fake = fake.replace("    print(json.dumps(dict(kind='command_result'",
                                        "    if c['command']=='cc' and c['value']==0:\n        import time; time.sleep(30)\n    print(json.dumps(dict(kind='command_result'")
                b = Bridge([sys.executable,'-u','-c',fake],.2)
                async with Client(create_server(b)) as client:
                    state = value(await client.call_tool('mainstage_refresh'))
                    args = dict(expected_session=state['session'], expected_revision=state['revision'])
                    if case == 'full':
                        invalid = await client.call_tool('mainstage_toggle_metronome',dict(args,expected_revision=True))
                        self.assertTrue(invalid.is_error)
                        self.assertEqual(b.counter,1)  # Invalid arguments never reach the native bridge.
                        invalid = await client.call_tool('mainstage_toggle_metronome',dict(args,expected_session='wrong'))
                        self.assertTrue(invalid.is_error)
                        self.assertIn('context changed',invalid.content[0].text)
                    if case == 'cancel':
                        task = asyncio.create_task(b.mutate('toggle_metronome', **args))
                        async with asyncio.timeout(1):
                            while b.counter < 4: await asyncio.sleep(.001)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError): await task
                        self.assertTrue(b.snapshot().stale)
                        self.assertIn('may have triggered', b.snapshot().reason)
                        continue
                    result = await client.call_tool('mainstage_toggle_metronome',args)
                    commands = [json.loads(line) for line in log.read_text().splitlines()]
                    cc = [{k:c[k] for k in ('control','value','channel')} for c in commands if c['command']=='cc']
                    if case == 'absent':
                        self.assertTrue(result.is_error)
                        self.assertIn('does not advertise', result.content[0].text)
                        self.assertEqual(cc, [])
                    else:
                        self.assertEqual(cc, [dict(control=80,value=127,channel=16),dict(control=80,value=0,channel=16)])
                        result = value(result)
                        self.assertFalse(result['observed'])
                        self.assertEqual(result['sent'],case == 'full')
                        self.assertEqual(result['messages_confirmed'],2 if case == 'full' else 1)
                        if case == 'partial': self.assertTrue(result['may_have_triggered'])

    async def test_action_timeout_after_confirmed_press_reports_precise_reason(self):
        fake = FAKE.replace('selection,patch_list,raw_midi_cc',
                            'selection,patch_list,raw_midi_cc,metronome_toggle')
        fake = fake.replace("    print(json.dumps(dict(kind='command_result'",
                            "    if c['command']=='cc' and c['value']==0:\n        import time; time.sleep(30)\n    print(json.dumps(dict(kind='command_result'")
        b = Bridge([sys.executable,'-u','-c',fake],.2)
        await b.start()
        try:
            state = await b.refresh()
            result = await b.mutate('toggle_metronome', state.session, state.revision)
            self.assertTrue(result['may_have_triggered'])
            self.assertFalse(result['sent'])
            self.assertEqual(result['messages_confirmed'], 1)
            self.assertTrue(b.snapshot().stale)
            self.assertEqual(b.snapshot().reason, 'Action press may have triggered; release not confirmed')
        finally: await b.close()

    async def test_bank_program_is_serialized_strict_and_truthful(self):
        with tempfile.TemporaryDirectory() as directory:
            for partial in (False, True):
                log = Path(directory)/str(partial)
                fake = FAKE.replace('    c=json.loads(line)',
                                    '    c=json.loads(line)\n    with open('+repr(str(log))+",'a') as log: log.write(json.dumps(c)+'\\n')")
                if partial:
                    fake = fake.replace("    print(json.dumps(dict(kind='command_result'",
                                        "    if c['command']=='cc' and c['control']==32:\n        import time; time.sleep(30)\n    print(json.dumps(dict(kind='command_result'")
                b = Bridge([sys.executable, '-u', '-c', fake], .2)
                async with Client(create_server(b)) as client:
                    state = value(await client.call_tool('mainstage_refresh'))
                    args = dict(expected_session=state['session'], expected_revision=state['revision'],
                                bank_msb=1, bank_lsb=2, program=3, channel=4)
                    if not partial:
                        invalid = await client.call_tool('mainstage_select_bank_program',
                                                         dict(args, bank_msb=True))
                        self.assertTrue(invalid.is_error)
                    result = value(await client.call_tool('mainstage_select_bank_program', args))
                    commands = [json.loads(line) for line in log.read_text().splitlines()]
                    mutation = [command for command in commands if command['command'] != 'refresh']
                    if partial:
                        self.assertEqual(result['messages_attempted'], 2)
                        self.assertEqual(result['messages_confirmed'], 1)
                        self.assertFalse(result['sent']); self.assertTrue(result['may_have_changed_bank'])
                        self.assertFalse(result['may_have_selected'])
                        self.assertEqual([command['control'] for command in mutation], [0, 32])
                    else:
                        self.assertEqual(
                            [{key: command[key] for key in command if key not in ('id', 'command')}
                             for command in mutation],
                            [dict(control=0, value=1, channel=4),
                             dict(control=32, value=2, channel=4),
                             dict(program=3, channel=4)])
                        self.assertTrue(result['sent']); self.assertFalse(result['observed'])
                        self.assertTrue(result['program_observed']); self.assertFalse(result['bank_observed'])
                        self.assertEqual(result['messages_attempted'], 3)
                        self.assertEqual(result['messages_confirmed'], 3)

    async def test_empty_expected_session_is_rejected_at_schema_level(self):
        b = Bridge([sys.executable, '-u', '-c', FAKE], .5)
        async with Client(create_server(b)) as client:
            state = value(await client.call_tool('mainstage_refresh'))
            args = dict(expected_session='', expected_revision=state['revision'])
            for tool, extra in (('mainstage_select_program', dict(program=0)),
                                ('mainstage_select_bank_program', dict(bank_msb=0, bank_lsb=0, program=0)),
                                ('mainstage_send_cc', dict(control=7, value=10)),
                                ('mainstage_toggle_metronome', {}),
                                ('mainstage_trigger_action', dict(action='metronome')),
                                ('mainstage_set_mapped_parameter_1', dict(value=0))):
                rejected = await client.call_tool(tool, dict(args, **extra))
                self.assertTrue(rejected.is_error, tool)
            self.assertEqual(b.counter, 1, 'Schema-invalid session strings never reach the native bridge')

    def test_main_rejects_blank_or_unsafe_endpoint_names(self):
        for argv in (['--bridge', 'helper', '--input', ''],
                     ['--bridge', 'helper', '--input', '   '],
                     ['--bridge', 'helper', '--input', 'In\nPut'],
                     ['--bridge', 'helper', '--input', 'In\x00Put'],
                     ['--bridge', 'helper', '--output', ''],
                     ['--bridge', 'helper', '--output', '\t'],
                     ['--bridge', 'helper', '--output', 'Out\x00Put'],
                     ['--bridge', 'helper', '--input', 'ok', '--output', '']):
            with redirect_stderr(io.StringIO()):
                with patch('mainstage_mcp.server.create_server',
                           side_effect=AssertionError('endpoint validation must run first')):
                    with self.assertRaises(SystemExit) as exited:
                        main(argv)
            self.assertEqual(exited.exception.code, 2)

    async def test_real_stdio_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory)/'fake_server.py'
            script.write_text('import sys\nfrom mainstage_mcp.server import Bridge,create_server\n'
                              + 'create_server(Bridge([sys.executable,"-u","-c",'
                              + repr(FAKE) + '])).run(transport="stdio")\n')
            params = StdioServerParameters(command=sys.executable,args=[str(script)],env=dict(os.environ))
            for version in ('2026-07-28', '2025-11-25'):
                async with Client(params, mode='legacy' if version == '2025-11-25' else version) as client:
                    self.assertEqual(client.protocol_version,version)
                    state = value(await client.call_tool('mainstage_refresh'))
                    self.assertTrue(state['session'].endswith('.fake-session'))
                    self.assertTrue(state['host_responsive'])


if __name__ == '__main__':
    unittest.main()
