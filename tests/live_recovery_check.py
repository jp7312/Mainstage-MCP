"""Live Python/native helper recovery check; tests/live_check.py covers MCP stdio.

Read-only by default.  ``--crash-helper`` kills only the subprocess owned by
this check and never sends Program Change, Control Change, or named actions.
"""
import argparse
import asyncio
import json
from pathlib import Path

from mainstage_mcp.server import Bridge


async def check(args):
    bridge = Bridge([str(Path(args.bridge).resolve()), '--iac-input', args.input,
                     '--iac-output', args.output], 5)
    await bridge.start()
    try:
        initial = await bridge.refresh()
        if (initial.stale or not initial.host_responsive or
                not initial.bridge_running or not initial.transport_connected):
            raise RuntimeError('Initial state is not fresh and connected; helper crash not attempted')
        if initial.selection is None or initial.selection.concert != args.concert:
            raise RuntimeError('Exact disposable concert differs; helper crash not attempted')
        if not initial.session or bridge.route is None:
            raise RuntimeError('Initial session or pinned route is missing; helper crash not attempted')
        route = bridge.route
        print(json.dumps(dict(initial=initial.model_dump()), ensure_ascii=False))
        if not args.crash_helper:
            return

        process, reader = bridge.process, bridge.reader
        if process is None or reader is None or process.returncode is not None:
            raise RuntimeError('Owned helper is not running; helper crash not attempted')
        process.kill()
        async with asyncio.timeout(bridge.timeout):
            await process.wait()
            await reader

        crashed = bridge.snapshot()
        if (crashed.bridge_running or crashed.transport_connected or
                crashed.host_responsive or not crashed.stale):
            raise RuntimeError('Helper crash did not mark bridge and transport unavailable')
        if crashed.session != initial.session or crashed.selection != initial.selection:
            raise RuntimeError('Helper crash did not preserve the stale session and selection')
        if bridge.route != route:
            raise RuntimeError('Pinned route changed after helper crash')
        print(json.dumps(dict(crashed=crashed.model_dump()), ensure_ascii=False))

        recovered = await bridge.reconnect()
        if (recovered.stale or not recovered.host_responsive or
                not recovered.bridge_running or not recovered.transport_connected):
            raise RuntimeError('Reconnect did not produce fresh connected state')
        if recovered.selection is None or recovered.selection.concert != args.concert:
            raise RuntimeError('Exact disposable concert changed after reconnect')
        if not recovered.session or recovered.session == initial.session:
            raise RuntimeError('Reconnect did not produce a different nonempty session')
        if bridge.route != route:
            raise RuntimeError('Reconnect did not preserve the pinned route')
        print(json.dumps(dict(recovered=recovered.model_dump()), ensure_ascii=False))
    finally:
        await bridge.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', required=True, help='native bridge executable')
    parser.add_argument('--concert', required=True, help='Exact disposable concert name')
    parser.add_argument('--input', default='MS Bridge Input')
    parser.add_argument('--output', default='MS Bridge Output')
    parser.add_argument('--crash-helper', action='store_true',
                        help='Kill only this check\'s helper, then reconnect it once')
    asyncio.run(check(parser.parse_args()))


if __name__ == '__main__':
    main()
