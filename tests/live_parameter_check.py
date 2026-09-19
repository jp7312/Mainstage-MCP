"""One-shot experimental mapped screen-control probe; verify plug-in/UI effect manually."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from live_check import result_value
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def midi_value(text):
    value = int(text)
    if not 0 <= value <= 127:
        raise argparse.ArgumentTypeError('value must be 0..127')
    return value


async def check(args):
    params = StdioServerParameters(command=sys.executable, args=[
        '-m', 'mainstage_mcp', 'serve', '--bridge', str(Path(args.bridge).resolve()),
        '--input', args.input, '--output', args.output, '--timeout', '5'])
    async with Client(params) as client:
        state = result_value(await client.call_tool('mainstage_refresh'))
        if state.get('stale') is not False or state.get('host_responsive') is not True:
            raise RuntimeError('MainStage is not responsive with fresh state; no mutation sent')
        if (not isinstance(state.get('session'), str) or not state['session']
                or type(state.get('revision')) is not int):
            raise RuntimeError('Fresh session and revision are unavailable; no mutation sent')
        selection = state.get('selection')
        if not selection or selection.get('concert') != args.concert:
            raise RuntimeError('Concert name differs; no mutation sent')
        if 'mapped_parameter_1' not in state.get('capabilities', []):
            raise RuntimeError('Profile does not advertise mapped_parameter_1; no mutation sent')
        baseline = state.get('mapped_parameter')
        if (not baseline or baseline.get('stale') is not False
                or baseline.get('id') != 'mapped_parameter_1'
                or baseline.get('source') != 'screen_control_feedback'
                or baseline.get('session') != state.get('session')
                or baseline.get('selectionRevision') != state.get('revision')
                or type(baseline.get('sequence')) is not int or baseline['sequence'] < 1
                or type(baseline.get('rawValue')) is not int
                or not 0 <= baseline['rawValue'] <= 127):
            raise RuntimeError('No current mapped feedback baseline;'
                               ' establish the mapping and move the control manually')
        if args.value == baseline.get('rawValue'):
            raise RuntimeError('Value matches the mapped feedback baseline; choose a different value')

        print(json.dumps(dict(baseline=baseline), ensure_ascii=False))
        result = result_value(await client.call_tool('mainstage_set_mapped_parameter_1', dict(
            value=args.value, expected_session=state['session'], expected_revision=state['revision'])))
        print(json.dumps(dict(result=result), ensure_ascii=False))
        result_state = result.get('state') if isinstance(result, dict) else None
        feedback = result_state.get('mapped_parameter') if isinstance(result_state, dict) else None
        if (result.get('sent') is not True or result.get('observed') is not True
                or not feedback or feedback.get('stale') is not False
                or feedback.get('id') != 'mapped_parameter_1'
                or feedback.get('source') != 'screen_control_feedback'
                or feedback.get('session') != state['session']
                or feedback.get('selectionRevision') != state['revision']
                or feedback.get('rawValue') != args.value
                or feedback.get('sequence', 0) <= baseline.get('sequence', 0)):
            raise RuntimeError('Newer matching screen-control feedback was not observed; do not retry automatically')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', required=True)
    parser.add_argument('--concert', required=True, help='Exact disposable concert name')
    parser.add_argument('--input', default='MS Bridge Input')
    parser.add_argument('--output', default='MS Bridge Output')
    parser.add_argument('--value', required=True, type=midi_value,
                        help='Explicitly allow one CC90 write (0..127); no retry or restore')
    asyncio.run(check(parser.parse_args()))


if __name__ == '__main__':
    main()
