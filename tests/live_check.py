"""MainStage check. Reads by default; --reconnect restarts the helper, --program changes patches."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def program_number(text):
    value = int(text)
    if not 0 <= value <= 127:
        raise argparse.ArgumentTypeError('program must be 0..127')
    return value


def result_value(result):
    if result.is_error:
        raise RuntimeError(str(result.content))
    return result.structured_content or json.loads(result.content[0].text)


async def check(args):
    params = StdioServerParameters(command=sys.executable, args=[
        '-m', 'mainstage_mcp', 'serve', '--bridge', str(Path(args.bridge).resolve()),
        '--input', args.input, '--output', args.output, '--timeout', '5'])
    async with Client(params) as client:
        state = result_value(await client.call_tool('mainstage_refresh'))
        if state['stale'] or not state['host_responsive']:
            raise RuntimeError('MainStage is not responsive with fresh state')
        if state['selection']['concert'] != args.concert:
            raise RuntimeError('Concert name differs; no mutation sent')
        print(json.dumps(dict(protocol=client.protocol_version, state=state), ensure_ascii=False))
        if args.reconnect:
            previous_session = state['session']
            if not previous_session:
                raise RuntimeError('Initial session is empty; no reconnect sent')
            state = result_value(await client.call_tool('mainstage_reconnect'))
            if state['stale'] or not state['host_responsive']:
                raise RuntimeError('Reconnect did not produce fresh responsive state')
            if state['selection']['concert'] != args.concert:
                raise RuntimeError('Concert context changed after reconnect; no mutation sent')
            if not state['session'] or state['session'] == previous_session:
                raise RuntimeError('Reconnect did not produce a new nonempty session')
            print(json.dumps(dict(reconnected=True, state=state), ensure_ascii=False))
        for program in args.program:
            # Names are a test guard, not a durable concert identity or transaction.
            state = result_value(await client.call_tool('mainstage_refresh'))
            if state['stale'] or state['selection']['concert'] != args.concert:
                raise RuntimeError('Concert context changed; no further mutation sent')
            result = result_value(await client.call_tool('mainstage_select_program', dict(
                program=program, expected_session=state['session'], expected_revision=state['revision'])))
            print(json.dumps(result, ensure_ascii=False))
            if not result.get('sent') or not result.get('observed'):
                raise RuntimeError('Program selection was not observed; stopping without retry')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', required=True)
    parser.add_argument('--concert', required=True, help='Exact disposable concert name')
    parser.add_argument('--input', default='MS Bridge Input')
    parser.add_argument('--output', default='MS Bridge Output')
    parser.add_argument('--reconnect', action='store_true',
                        help='Explicitly restart the owned helper once and verify a fresh same-concert session')
    parser.add_argument('--program', type=program_number, action='append', default=[],
                        help='Explicitly allow this program change; repeat for a sequence')
    asyncio.run(check(parser.parse_args()))


if __name__ == '__main__':
    main()
