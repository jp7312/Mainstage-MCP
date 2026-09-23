"""Local MCP tools. The official SDK owns the MCP protocol; MIDI is JSONL."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import secrets
import time
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, StrictInt

from mainstage_mcp import __version__

MidiByte = Annotated[StrictInt, Field(ge=0, le=127)]
Channel = Annotated[StrictInt, Field(ge=1, le=16)]
Revision = Annotated[StrictInt, Field(ge=0)]
Session = Annotated[str, Field(min_length=1)]
MAX_LINE = 512 * 1024  # JSON escaping can expand a 64 KiB SysEx frame.
MAX_SNAPSHOT = 4 * 1024 * 1024
BASE_CAPABILITIES = {'selection', 'patch_list', 'raw_midi_cc'}
ACTION_BINDINGS = {
    'metronome': ('action_metronome', 80, 'Metronome'),
    'panic': ('action_panic', 85, 'Panic'),
    'master_mute': ('action_master_mute', 86, 'Master Mute'),
    'play_stop': ('action_play_stop', 87, 'Play/Stop'),
}
ActionName = Literal['metronome', 'panic', 'master_mute', 'play_stop']
ALLOWED_CAPABILITIES = BASE_CAPABILITIES | {'metronome_toggle', 'mapped_parameter_1'} | {
    binding[0] for binding in ACTION_BINDINGS.values()}


class Selection(BaseModel):
    program: int
    setIndex: int
    patchIndex: int
    concert: str
    set: str
    patch: str


class Item(BaseModel):
    isPatch: bool
    setIndex: int
    patchIndex: int
    label: str


class MappedParameter(BaseModel):
    id: str
    session: str
    selectionRevision: int
    sequence: int
    rawValue: int
    minimum: int
    maximum: int
    rawUnit: str
    label: str
    display: str
    source: str
    observedAt: float
    stale: bool
    reason: str | None


class State(BaseModel):
    bridge_running: bool
    transport_connected: bool
    profile_seen: bool
    host_responsive: bool
    stale: bool
    reason: str | None
    application: str | None
    session: str | None
    revision: int | None
    selection: Selection | None
    items: list[Item] | None
    observed_at: float | None
    responsive_at: float | None
    capabilities: list[str] = Field(default_factory=list)
    mapped_parameter: MappedParameter | None = None


def number(value: str, low: int, high: int) -> int:
    if not re.fullmatch(r'-?[0-9]{1,16}', value):
        raise ValueError('invalid integer feedback')
    result = int(value)
    if not low <= result <= high:
        raise ValueError('feedback integer out of range')
    return result


class Bridge:
    def __init__(self, command: list[str], timeout: float = 3.0):
        if not command or not 0 < timeout <= 30:
            raise ValueError('bridge command and timeout (0,30] required')
        self.command, self.timeout = command, timeout
        self.process = None
        self.reader = None
        self.lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future] = {}
        self.responses: dict[str, asyncio.Future] = {}
        self.counter = 0
        self.request_prefix = secrets.token_hex(8)
        self.connection_prefix = secrets.token_hex(8)
        self.route = None
        self.transaction = None
        self.session = None
        self.application = None
        self.capabilities = []
        self.connected = False
        self.ready = asyncio.Event()
        self.profile_seen = False
        self.reason = 'not started'
        self.stale = True
        self.complete = None
        self.responsive_at = None
        self.responsive_clock = None
        self.parameter = None

    async def start(self):
        self.ready.clear()
        self.connected = False
        self.profile_seen = False
        self.capabilities = []
        self.connection_prefix = secrets.token_hex(8)
        self.invalidate('bridge starting')
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=None, limit=MAX_LINE)
        self.reader = asyncio.create_task(self.read(self.process))

    def invalidate(self, reason: str):
        self.stale, self.reason, self.transaction = True, reason, None
        self.responsive_clock = None

    def snapshot(self) -> State:
        data = copy.deepcopy(self.complete or {})
        parameter = copy.deepcopy(self.parameter) if data.get('client_session') else None
        responsive = (self.responsive_clock is not None and
                      time.monotonic() - self.responsive_clock <= self.timeout)
        if parameter:
            reasons = []
            if parameter['session'] != self.session:
                reasons.append('profile session changed')
            if parameter.get('client_session') != data['client_session']:
                reasons.append('connection session changed')
            if 'mapped_parameter_1' not in self.capabilities:
                reasons.append('mapped parameter capability unavailable')
            if not data or parameter['selectionRevision'] != data.get('revision'):
                reasons.append('selection revision changed')
            if self.stale or not responsive or not self.connected:
                reasons.append(self.reason or 'host responsiveness expired')
            parameter['session'] = parameter.pop('client_session')
            parameter['stale'], parameter['reason'] = bool(reasons), '; '.join(dict.fromkeys(reasons)) or None
        return State(bridge_running=self.process is not None and self.process.returncode is None,
                     transport_connected=self.connected, profile_seen=self.profile_seen,
                     host_responsive=responsive and self.connected,
                     stale=self.stale or not responsive or not self.connected,
                     reason=self.reason if self.stale else (None if responsive else 'responsiveness expired'),
                     application=self.application, session=data.get('client_session'),
                     revision=data.get('revision'), selection=data.get('selection'),
                     items=data.get('items'), observed_at=data.get('observed_at'),
                     responsive_at=self.responsive_at, capabilities=list(self.capabilities),
                     mapped_parameter=parameter)

    def accept(self, event: dict):
        if not isinstance(event, dict):
            raise ValueError('event must be an object')
        kind = event.get('kind')
        if kind == 'transport':
            if type(event.get('connected')) is not bool:
                raise ValueError('invalid transport status')
            if event['connected']:
                route = event.get('route')
                if (not isinstance(route, list) or len(route) != 2 or
                        any(not isinstance(row, list) or len(row) != 6 for row in route) or
                        [row[0] for row in route] != ['destination', 'source'] or
                        any(not isinstance(row[1], str) or not row[1] or
                            any(type(value) is not int or value == 0 for value in row[2:5]) or
                            not isinstance(row[5], str) or not row[5] for row in route)):
                    raise ValueError('invalid transport route identity')
                route = tuple(tuple(row) for row in route)
                if self.route is None:
                    self.route = route
                elif route != self.route:
                    self.connected = False
                    self.ready.set()
                    self.invalidate('endpoint identity changed; explicit reconnect refused')
                    return
            self.connected = event['connected']
            self.ready.set()
            if not self.connected:
                self.invalidate(str(event.get('reason', 'transport disconnected')))
            return
        if kind == 'command_result':
            ident = event.get('id')
            if type(event.get('ok')) is not bool or type(event.get('status')) is not int:
                raise ValueError('invalid command result')
            future = self.pending.get(ident)
            if future and not future.done():
                future.set_result(event)
            return
        counts = {'hello': 4, 'snapshot_begin': 3, 'selection': 6,
                  'item': 4, 'snapshot_end': 4, 'parameter': 11,
                  'goodbye': 1, 'error': 3}
        fields = event.get('fields')
        if kind == 'hello':
            self.profile_seen = False
            self.capabilities = []
        if (kind not in counts or not isinstance(fields, list) or len(fields) != counts[kind]
                or not all(isinstance(f, str) for f in fields)):
            raise ValueError('invalid feedback event')
        if sum(len(f.encode('utf-8')) for f in fields) > 65536:
            raise ValueError('feedback frame too large')
        if kind == 'hello':
            if fields[0] != 'MainStage' or fields[1] != '2' or not fields[2]:
                raise ValueError('unsupported profile')
            if self.session != fields[2]:
                self.invalidate('profile session changed')
            capabilities = fields[3].split(',')
            if (len(capabilities) != len(set(capabilities)) or
                    not BASE_CAPABILITIES <= set(capabilities) <= ALLOWED_CAPABILITIES):
                raise ValueError('unsupported profile capabilities')
            self.application, _, self.session, _ = fields
            self.capabilities = capabilities
            self.profile_seen = True
        elif kind == 'goodbye':
            if fields[0] == self.session:
                self.profile_seen = False
                self.capabilities = []
                self.invalidate('profile finalized')
        elif kind == 'error':
            if not fields[0] or fields[0] in self.responses:
                self.invalidate(': '.join(fields[1:]))
            future = self.responses.get(fields[0])
            if future and not future.done():
                future.set_result({'error': ': '.join(fields[1:])})
        elif kind == 'parameter':
            if 'mapped_parameter_1' not in self.capabilities or fields[0] != self.session:
                raise ValueError('parameter feedback without matching capability/session')
            if (fields[3] != 'mapped_parameter_1' or fields[5:8] != ['0', '127', 'midi_7bit']
                    or fields[10] != 'screen_control_feedback'):
                raise ValueError('unsupported parameter mapping')
            value = dict(id=fields[3], session=fields[0],
                         selectionRevision=number(fields[1], 0, 2**53 - 1),
                         sequence=number(fields[2], 1, 2**53 - 1),
                         rawValue=number(fields[4], 0, 127), minimum=0, maximum=127,
                         rawUnit='midi_7bit', label=fields[8], display=fields[9],
                         source=fields[10])
            previous = self.parameter
            if previous and previous['session'] == value['session']:
                if value['sequence'] < previous['sequence']:
                    raise ValueError('parameter feedback sequence regressed')
                if value['sequence'] == previous['sequence']:
                    if any(previous[key] != value[key] for key in value):
                        raise ValueError('parameter feedback changed without sequence')
                    return
            value['client_session'] = f'{self.connection_prefix}.{fields[0]}'
            value['observedAt'] = time.time()
            self.parameter = value
        elif kind == 'snapshot_begin':
            future = self.responses.get(fields[2])
            if fields[2] and (future is None or future.done()):
                # Consume delayed tagged replies without changing current state or freshness.
                self.transaction = dict(ignored=True)
                return
            if self.transaction and not self.transaction.get('ignored'):
                raise ValueError('overlapping snapshot')
            self.invalidate('snapshot incomplete')
            if fields[0] != self.session or not self.profile_seen:
                raise ValueError('snapshot session mismatch')
            rev = number(fields[1], 0, 2**53 - 1)
            if self.complete and self.complete['session'] == self.session and rev < self.complete['revision']:
                raise ValueError('snapshot revision regressed')
            self.transaction = dict(session=fields[0], client_session=f'{self.connection_prefix}.{fields[0]}',
                                    revision=rev, request=fields[2], items=[], selection=None, size=0)
        elif kind in ('selection', 'item', 'snapshot_end'):
            tx = self.transaction
            if tx is None:
                raise ValueError('feedback outside snapshot')
            if tx.get('ignored'):
                if kind == 'snapshot_end':
                    self.transaction = None
                return
            tx['size'] += sum(len(f.encode('utf-8')) for f in fields)
            if tx['size'] > MAX_SNAPSHOT:
                raise ValueError('snapshot too large')
            if kind == 'selection':
                if tx['selection'] is not None:
                    raise ValueError('duplicate selection')
                tx['selection'] = dict(program=number(fields[0], -1, 127),
                    setIndex=number(fields[1], -1, 2**31-1), patchIndex=number(fields[2], -1, 2**31-1),
                    concert=fields[3], set=fields[4], patch=fields[5])
            elif kind == 'item':
                if len(tx['items']) >= 4096 or fields[0] not in ('0', '1'):
                    raise ValueError('invalid/excess snapshot item')
                tx['items'].append(dict(isPatch=fields[0] == '1',
                    setIndex=number(fields[1], -1, 2**31-1),
                    patchIndex=number(fields[2], -1, 2**31-1), label=fields[3]))
            else:
                future = self.responses.get(tx['request'])
                if tx['request'] and (future is None or future.done()):
                    self.transaction = None
                    return
                if (fields[:3] != [tx['session'], str(tx['revision']), tx['request']] or
                        number(fields[3], 0, 4096) != len(tx['items']) or tx['selection'] is None):
                    self.transaction = None  # A rejected end marker terminates the cycle.
                    raise ValueError('snapshot end mismatch')
                if (self.complete and self.complete['session'] == tx['session'] and
                        self.complete['revision'] == tx['revision'] and
                        any(self.complete[k] != tx[k] for k in ('selection', 'items'))):
                    self.transaction = None
                    raise ValueError('snapshot content changed without revision')
                self.complete = {k: tx[k] for k in ('session', 'client_session', 'revision', 'items', 'selection')}
                self.complete['observed_at'] = time.time()
                self.transaction = None
                self.stale, self.reason = False, None
                self.responsive_clock, self.responsive_at = time.monotonic(), time.time()
                if future and not future.done():
                    future.set_result(copy.deepcopy(self.complete))

    async def read(self, process):
        try:
            while line := await process.stdout.readline():
                try:
                    self.accept(json.loads(line))
                except (ValueError, TypeError, KeyError) as error:
                    self.invalidate(str(error))
        except (ValueError, OSError) as error:
            self.invalidate(f'bridge read failed: {error}')
        finally:
            self.connected = False
            self.ready.set()
            self.invalidate('bridge output closed')
            for future in [*self.pending.values(), *self.responses.values()]:
                if not future.done():
                    future.set_result({'error': 'bridge output closed'})
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()

    async def send(self, command: str, **values):
        if not self.ready.is_set():
            await self.ready.wait()
        if (not self.connected or self.process is None or self.process.returncode is not None
                or getattr(self.process, 'stdin', None) is None):
            raise ValueError('bridge transport unavailable; restart after fixing endpoints')
        self.counter += 1
        ident = f'{self.request_prefix}_{self.counter}'
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        if command == 'refresh':
            self.responses[ident] = asyncio.get_running_loop().create_future()
        try:
            self.process.stdin.write((json.dumps(dict(id=ident, command=command, **values)) + '\n').encode())
            await self.process.stdin.drain()
            result = await future
            if not result.get('ok'):
                raise ValueError(f'MIDI send failed: {result}')
            if command == 'refresh':
                reply = await self.responses[ident]
                if 'error' in reply:
                    raise ValueError(reply['error'])
                return reply
            return result
        finally:
            self.pending.pop(ident, None)
            self.responses.pop(ident, None)

    async def refresh(self) -> State:
        acquired = False
        try:
            async with asyncio.timeout(self.timeout):
                async with self.lock:
                    acquired = True
                    self.invalidate('refresh pending')
                    await self.send('refresh')
                    return self.snapshot()
        except (TimeoutError, OSError, ValueError) as error:
            if acquired:
                self.invalidate(str(error) or 'refresh failed or timed out')
            raise ToolError(str(error) or 'Profile refresh timed out') from error

    async def mutate(self, command: str, expected_session: Session, expected_revision: int, **values) -> dict:
        acquired = False
        sent = False
        attempted = False
        confirmed = 0
        attempted_messages = 0
        statuses = []
        action = 'metronome' if command == 'toggle_metronome' else values.get('action')
        action_binding = ACTION_BINDINGS.get(action)
        is_action = command in ('toggle_metronome', 'action')
        try:
            async with asyncio.timeout(self.timeout):
                async with self.lock:
                    acquired = True
                    await self.send('refresh')
                    current = self.complete
                    if self.snapshot().stale:
                        raise ValueError('profile became stale before mutation')
                    if (current['client_session'], current['revision']) != (expected_session, expected_revision):
                        raise ValueError('context changed; read state and explicitly choose again')
                    if is_action:
                        if action_binding is None:
                            raise ValueError('unsupported named action')
                        capability = 'metronome_toggle' if command == 'toggle_metronome' else action_binding[0]
                        if capability not in self.capabilities:
                            raise ValueError(f'profile does not advertise {capability}')
                    if command == 'set_mapped_parameter_1' and 'mapped_parameter_1' not in self.capabilities:
                        raise ValueError('profile does not advertise mapped_parameter_1')
                    baseline = (self.parameter['sequence'] if command == 'set_mapped_parameter_1'
                                and self.parameter and self.parameter['session'] == self.session else 0)
                    self.invalidate('MIDI mutation pending; refresh required')
                    attempted = True
                    if is_action:
                        for value in (127, 0):
                            await self.send('cc', control=action_binding[1], value=value, channel=16)
                            confirmed += 1
                        experimental = action != 'metronome'
                        press_note = f'{"Experimental " if experimental else ""}{action_binding[2]} press/release sent'
                        return dict(action=action, experimental=experimental, sent=True, observed=False,
                                    messages_confirmed=confirmed,
                                    note=press_note + '; action result is not confirmed.')
                    if command == 'set_mapped_parameter_1':
                        receipt = await self.send('cc', control=90, value=values['value'], channel=16)
                    elif command == 'bank_pc':
                        for midi_command, midi_values in (
                            ('cc', dict(control=0, value=values['bank_msb'], channel=values['channel'])),
                            ('cc', dict(control=32, value=values['bank_lsb'], channel=values['channel'])),
                            ('pc', dict(program=values['program'], channel=values['channel']))):
                            attempted_messages += 1
                            receipt = await self.send(midi_command, **midi_values)
                            statuses.append(receipt['status'])
                            confirmed += 1
                    else:
                        receipt = await self.send(command, **values)
                    sent = True
                    if command == 'cc':
                        return dict(sent=True, observed=False, midi_status=receipt['status'],
                                    note='Raw MIDI only; no mapped action or parameter feedback is claimed.')
                    # A subsequent correlated snapshot is evidence of the resulting selection,
                    # not proof that this command caused it (it may already have been selected).
                    while True:
                        await self.send('refresh')
                        result = self.complete  # Include any newer unsolicited snapshot already read.
                        context_changed = (result['client_session'] != expected_session
                                           or result['selection']['concert'] != current['selection']['concert']
                                           or (command == 'set_mapped_parameter_1'
                                               and result['revision'] != expected_revision))
                        if context_changed:
                            self.invalidate('concert or profile session changed while awaiting selection')
                            if command == 'bank_pc':
                                return dict(sent=True, observed=False, program_observed=False,
                                            bank_observed=False, context_changed=True,
                                            messages_attempted=attempted_messages,
                                            messages_confirmed=confirmed, midi_statuses=statuses,
                                            state=self.snapshot().model_dump())
                            return dict(sent=True, observed=False, context_changed=True,
                                        midi_status=receipt['status'], state=self.snapshot().model_dump())
                        if self.snapshot().stale:
                            raise ValueError('profile became stale while awaiting selection')
                        if command == 'set_mapped_parameter_1':
                            feedback = self.parameter
                            if (feedback and feedback['session'] == self.session
                                    and feedback['selectionRevision'] == result['revision']
                                    and feedback['sequence'] > baseline
                                    and feedback['id'] == 'mapped_parameter_1'
                                    and feedback['source'] == 'screen_control_feedback'
                                    and feedback['rawValue'] == values['value']):
                                return dict(sent=True, observed=True, context_changed=False,
                                            feedback='mapped_screen_control', midi_status=receipt['status'],
                                            state=self.snapshot().model_dump(),
                                            note=('A newer matching screen-control callback was'
                                                  ' observed; underlying plug-in effect is not proven.'))
                            self.invalidate('awaiting newer mapped screen-control feedback')
                            await asyncio.sleep(.05)
                            continue
                        if result['selection']['program'] == values['program']:
                            if command == 'bank_pc':
                                note = ('A fresh selection callback matched the program; MainStage does not report'
                                        ' the selected bank, so the bank/program target is not fully observed.')
                                return dict(sent=True, observed=False, program_observed=True,
                                            bank_observed=False, context_changed=False,
                                            messages_attempted=attempted_messages,
                                            messages_confirmed=confirmed, midi_statuses=statuses,
                                            state=self.snapshot().model_dump(), note=note)
                            return dict(sent=True, observed=True, context_changed=False,
                                        midi_status=receipt['status'], state=self.snapshot().model_dump())
                        # Host patch loading may lag MIDI delivery. Poll feedback only;
                        # the original PC is never retried, and the outer deadline still applies.
                        self.invalidate('awaiting requested program selection')
                        await asyncio.sleep(.05)
        except asyncio.CancelledError:
            if attempted:
                self.invalidate('Action press may have triggered; release not confirmed'
                                if is_action else
                                ('Bank/program mutation may be partial; refresh required'
                                 if command == 'bank_pc' else
                                 'MIDI mutation observation cancelled; refresh required'))
            raise
        except (TimeoutError, OSError, ValueError) as error:
            if acquired:
                self.invalidate(str(error) or
                                ('Action press may have triggered; release not confirmed'
                                 if is_action and confirmed else 'command timed out'))
            if is_action and attempted:
                return dict(action=action, experimental=action != 'metronome', sent=False,
                            observed=False, may_have_triggered=True, messages_confirmed=confirmed,
                            error=str(error) or 'action transport timed out',
                            note='Press may have triggered; release is not confirmed. Do not automatically retry.')
            if command == 'bank_pc' and attempted:
                return dict(sent=sent, observed=False, program_observed=False, bank_observed=False,
                            messages_attempted=attempted_messages,
                            messages_confirmed=confirmed, midi_statuses=statuses,
                            may_have_changed_bank=attempted_messages > 0,
                            may_have_selected=attempted_messages == 3,
                            timed_out=isinstance(error, TimeoutError),
                            error=str(error) or 'bank/program selection timed out',
                            state=self.snapshot().model_dump(),
                            note=('Do not automatically retry: the bank/program sequence'
                                  ' or its observation may be partial.'))
            if sent:
                return dict(sent=True, observed=False, timed_out=isinstance(error, TimeoutError),
                            error=str(error) or 'feedback timed out', state=self.snapshot().model_dump())
            prefix = "MIDI may have been sent" if attempted else "No MIDI mutation sent"
            raise ToolError(f'{prefix}: {str(error) or "timeout"}') from error

    async def reconnect(self) -> State:
        async with self.lock:
            self.invalidate('explicit reconnect pending')
            try:
                await self.close('explicit reconnect pending')
                await self.start()
                async with asyncio.timeout(self.timeout):
                    await self.ready.wait()
                    if not self.connected:
                        raise ValueError(self.reason or 'bridge transport unavailable')
                    await self.send('refresh')
                    return self.snapshot()
            except asyncio.CancelledError:
                await self.close('explicit reconnect cancelled')
                raise
            except (TimeoutError, OSError, ValueError) as error:
                reason = str(error) or 'reconnect timed out'
                await self.close(reason)
                raise ToolError(reason) from error

    async def close(self, reason='bridge stopped'):
        if self.process is None:
            return
        try:
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 1)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        self.process.kill()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self.process.wait(), 1)
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
        finally:
            self.connected = False
            self.invalidate(reason)


def create_server(bridge: Bridge) -> MCPServer:
    @asynccontextmanager
    async def lifespan(server):
        await bridge.start()
        try:
            yield {}
        finally:
            await bridge.close()

    server = MCPServer('MainStage MCP', version=__version__, lifespan=lifespan)

    @server.tool(annotations={'readOnlyHint': True})
    async def mainstage_get_state() -> State:
        """Read last complete snapshot and explicit stale/liveness metadata; does not contact host."""
        return bridge.snapshot()

    @server.tool(annotations={'readOnlyHint': True})
    async def mainstage_list_patches() -> State:
        ("Read cached patch/set list with its session, revision and stale metadata."
         " Indices are not MIDI program numbers.")
        return bridge.snapshot()

    @server.tool(annotations={'readOnlyHint': True})
    async def mainstage_refresh() -> State:
        ("Request a correlated snapshot with a deadline, proving profile responsiveness."
         " Names remain callback-derived.")
        return await bridge.refresh()

    @server.tool(annotations={'readOnlyHint': True})
    async def mainstage_reconnect() -> State:
        ("Explicitly restart the native helper on its pinned endpoint identities, then refresh."
         " Never replay a mutation.")
        return await bridge.reconnect()

    @server.tool()
    async def mainstage_select_program(program: MidiByte, expected_session: Session, expected_revision: Revision,
                                       channel: Channel = 1) -> dict:
        ("Select the assigned MIDI program (0–127), not a list index."
         " Require last-read session/revision; report MIDI sent separately from observed selection.")
        return await bridge.mutate('pc', expected_session, expected_revision, program=program, channel=channel)

    @server.tool()
    async def mainstage_select_bank_program(bank_msb: MidiByte, bank_lsb: MidiByte, program: MidiByte,
                                            expected_session: Session, expected_revision: Revision,
                                            channel: Channel = 1) -> dict:
        ("Send Bank Select MSB, LSB, then Program Change once. MainStage assignments determine the result;"
         " bank state cannot be observed.")
        return await bridge.mutate('bank_pc', expected_session, expected_revision,
                                   bank_msb=bank_msb, bank_lsb=bank_lsb,
                                   program=program, channel=channel)

    @server.tool()
    async def mainstage_send_cc(control: MidiByte, value: MidiByte, expected_session: Session,
                                expected_revision: Revision, channel: Channel = 1) -> dict:
        ("Advanced raw MIDI CC. Mapping determines effect; no action completion or parameter feedback is"
         " available. Requires last-read session/revision.")
        return await bridge.mutate('cc', expected_session, expected_revision,
                                   control=control, value=value, channel=channel)

    @server.tool()
    async def mainstage_toggle_metronome(expected_session: Session, expected_revision: Revision) -> dict:
        ("Toggle metronome using a profile-advertised binding. Sends one press/release; no metronome on/off"
         " state or action completion feedback exists. Never automatically retry.")
        return await bridge.mutate('toggle_metronome', expected_session, expected_revision)

    @server.tool()
    async def mainstage_trigger_action(action: ActionName, expected_session: Session,
                                       expected_revision: Revision) -> dict:
        ("Trigger one profile-advertised named action. Non-metronome names are experimental. Sends one"
         " press/release and reports MIDI transport only; no action result or state is observed."
         " Never automatically retry.")
        return await bridge.mutate('action', expected_session, expected_revision, action=action)

    @server.tool()
    async def mainstage_set_mapped_parameter_1(value: MidiByte, expected_session: Session,
                                               expected_revision: Revision) -> dict:
        ("Experimental opt-in CC90 slot. Observe only a newer matching screen-control callback;"
         " never claim plug-in enumeration or effect.")
        return await bridge.mutate('set_mapped_parameter_1', expected_session, expected_revision, value=value)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=3.0)
    parser.add_argument('--bridge', required=True, help='native bridge executable')
    parser.add_argument('--input', default='MS Bridge Input')
    parser.add_argument('--output', default='MS Bridge Output')
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 30:
        parser.error('--timeout must be in (0, 30]')
    for flag, name in (('--input', args.input), ('--output', args.output)):
        if not name.strip() or '\x00' in name or '\n' in name:
            parser.error(f'{flag} must be a nonempty endpoint name without NUL or newline')
    command = [args.bridge, '--iac-input', args.input, '--iac-output', args.output]
    create_server(Bridge(command, args.timeout)).run(transport='stdio')


if __name__ == '__main__':
    main()
