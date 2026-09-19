"""Keep installation separate from starting an MCP session."""

import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("Usage: mainstage-mcp {serve|install|uninstall|doctor|inspect-concert} [options]")
        print("Use a subcommand with --help for options. Startup never changes MIDI setup.")
        return 0
    if args[0] == "--version":
        from . import __version__
        print(__version__)
        return 0
    if args[0] == "serve":
        from .server import main as serve
        return serve(args[1:])
    if args[0] in ("install", "uninstall", "doctor"):
        from .installation import main as setup
        return setup(args)
    if args[0] == "inspect-concert":
        from .concert_inspector import main as inspect
        return inspect(args[1:])
    print(f"Unknown command: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
