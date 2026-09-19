#!/usr/bin/env python3
"""Read the inspectable plist layer of one tested MainStage concert format."""
from __future__ import annotations

import argparse
import json
import plistlib
from pathlib import Path
from typing import Any

DOCUMENT_VERSION = 57057
PATCH_VERSION = 40014
MAX_PLIST_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PLIST_BYTES = 64 * 1024 * 1024
MAX_NODES = 4096
MAX_CHANNELS = 16384
MAX_DEPTH = 32


class ConcertFormatError(ValueError):
    pass


def _plist(path: Path, budget: list[int]) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ConcertFormatError(f"missing or unsafe plist: {path.name}")
        with path.open("rb") as stream:
            data = stream.read(MAX_PLIST_BYTES + 1)
    except OSError as error:
        raise ConcertFormatError(f"unreadable plist: {path.name}") from error
    if len(data) > MAX_PLIST_BYTES:
        raise ConcertFormatError(f"plist exceeds {MAX_PLIST_BYTES} bytes: {path.name}")
    if len(data) > budget[0]:
        raise ConcertFormatError("concert plists exceed inspection byte limit")
    budget[0] -= len(data)
    try:
        value = plistlib.loads(data)
    except Exception as error:
        raise ConcertFormatError(f"invalid plist: {path.name}") from error
    if not isinstance(value, dict):
        raise ConcertFormatError(f"plist root must be a dictionary: {path.name}")
    return value


def _integer(value: Any, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ConcertFormatError(f"invalid {field}")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ConcertFormatError(f"invalid {field}")
    return value


def _text(value: Any, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 1024 or (not empty and not value):
        raise ConcertFormatError(f"invalid {field}")
    if "\x00" in value:
        raise ConcertFormatError(f"{field} contains NUL")
    return value


def _safe_name(value: Any, suffix: str, field: str) -> str:
    name = _text(value, field)
    if Path(name).name != name or "/" in name or "\\" in name or not name.endswith(suffix):
        raise ConcertFormatError(f"unsafe {field}")
    return name


def _route(channel: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "is_bus": _boolean(channel.get(f"Channel_{prefix}IsBus"), f"{prefix} is_bus"),
        "index": _integer(channel.get(f"Channel_{prefix}Index_1" if prefix == "input" else f"Channel_{prefix}Index"),
                          f"{prefix} index", -1, 65535),
    }


def _channel(value: Any, node_dir: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConcertFormatError("channel must be a dictionary")
    result = {
        "name": _text(value.get("Channel_name"), "channel name", empty=True),
        "uuid": _text(value.get("UUID"), "channel UUID"),
        "instrument_id": _integer(value.get("Channel_instID"), "channel instrument_id", 0, 2**31 - 1),
        "input": _route(value, "input"),
        "output": _route(value, "output"),
        "muted": _boolean(value.get("Channel_isMuted"), "channel muted"),
        "solo": _boolean(value.get("Channel_isSolo"), "channel solo"),
    }
    filename = value.get("Filename")
    if filename is not None:
        filename = _safe_name(filename, ".cst", "channel setting filename")
        setting = node_dir / filename
        if setting.is_symlink():
            raise ConcertFormatError("unsafe channel setting symlink")
        result["opaque_setting_file"] = filename
        result["setting_present"] = setting.is_file()
    return result


def inspect_concert(path: str | Path) -> dict[str, Any]:
    root = Path(path)
    if root.is_symlink() or not root.is_dir() or root.suffix.lower() != ".concert":
        raise ConcertFormatError("expected a non-symlink .concert directory")
    budget = [MAX_TOTAL_PLIST_BYTES]
    document = _plist(root / "data.plist", budget)
    version = _integer(document.get("Version"), "document Version", 0, 2**31 - 1)
    if version != DOCUMENT_VERSION:
        raise ConcertFormatError(f"unsupported document Version {version}; expected {DOCUMENT_VERSION}")

    seen = channels_seen = 0

    def node(directory: Path, depth: int, root_node: bool = False) -> dict[str, Any]:
        nonlocal seen, channels_seen
        seen += 1
        if seen > MAX_NODES or depth > MAX_DEPTH:
            raise ConcertFormatError("concert hierarchy exceeds inspection bounds")
        if directory.is_symlink() or not directory.is_dir():
            raise ConcertFormatError("missing or unsafe patch directory")
        data = _plist(directory / "data.plist", budget)
        patch_version = _integer(data.get("VersionPatches"), "VersionPatches", 0, 2**31 - 1)
        if patch_version != PATCH_VERSION:
            raise ConcertFormatError(
                f"unsupported VersionPatches {patch_version}; expected {PATCH_VERSION}")
        patch = data.get("patch")
        engine = patch.get("engineNode") if isinstance(patch, dict) else None
        if not isinstance(engine, dict):
            raise ConcertFormatError("missing patch.engineNode")
        channels = data.get("channels", [])
        children = data.get("nodes", [])
        if not isinstance(channels, list) or len(channels) > MAX_NODES:
            raise ConcertFormatError("invalid channels")
        channels_seen += len(channels)
        if channels_seen > MAX_CHANNELS:
            raise ConcertFormatError("concert channels exceed inspection limit")
        if not isinstance(children, list) or len(children) > MAX_NODES:
            raise ConcertFormatError("invalid nodes")
        result: dict[str, Any] = {
            "kind": "concert" if root_node else ("container" if "nodes" in data else "patch"),
            "name": _text(engine.get("name"), "node name"),
            "channels": [_channel(channel, directory) for channel in channels],
            "children": [],
        }
        if _boolean(engine.get("hasProgramChange", False), "hasProgramChange"):
            result["program_change"] = {
                "program": _integer(engine.get("patchChangeNum"), "patchChangeNum", 0, 127),
                "bank": _integer(engine.get("bankSelectNumber", 0), "bankSelectNumber", 0, 16383),
                "bank_enabled": _boolean(engine.get("hasBankSelect", False), "hasBankSelect"),
            }
        for child in children:
            child_name = _safe_name(child, ".patch", "node filename")
            result["children"].append(node(directory / child_name, depth + 1))
        return result

    concert = node(root / "Concert.patch", 0, root_node=True)
    return {
        "schema": "mainstage-concert-inspection/v1",
        "format": {"document_version": version, "patch_version": PATCH_VERSION},
        "concert": concert,
        "limits": {
            "read_only": True,
            "plugin_state": "opaque",
            "tested_mainstage": "4.3.1 (5233)",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("concert", help="path to a .concert package")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_concert(args.concert), ensure_ascii=False, indent=2))
    except (ConcertFormatError, ValueError, OSError) as error:
        parser.exit(2, f"concert-inspector: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
