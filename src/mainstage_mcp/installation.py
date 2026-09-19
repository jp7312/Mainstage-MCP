"""Owned profile installation. Never creates, renames, or changes MIDI devices."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

PROFILE_ROOT = Path.home() / "Music/Audio Music Apps/MIDI Device Profiles"
STATE = Path.home() / "Library/Application Support/MainStage MCP/installation.json"
DRIVER = "com.apple.AppleMIDIIACDriver"
SYSTEM_PROFILE_ROOTS = (Path("/Library/Audio/MIDI Device Profiles"), Path("/Library/Application Support/Logic/MIDI Device Profiles"))
# Object handles can change between processes; persistent MIDI IDs cannot.
ENDPOINT_KEYS = ("direction", "name", "unique_id", "entity_unique_id", "device_unique_id", "device_name", "manufacturer", "model", "driver_owner")


def safe_path(value):
    path = Path(os.path.abspath(Path(value).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Refusing symbolic link: {part}")
    return path


def component(value):
    if not isinstance(value, str) or not value.strip() or value in (".", "..") or any(c in value for c in "/\\\x00\r\n"):
        raise ValueError("Unsafe or missing device metadata")
    return value


def normalized_component(value):
    return component(re.sub(r"[.\s]+$", "", unicodedata.normalize("NFC", component(value))))


def canonical(value):
    return normalized_component(value).casefold()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_exclusive(path, data):
    """Publish complete contents without replacing any existing file."""
    fd, temporary = tempfile.mkstemp(prefix=".mainstage-mcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def endpoints(bridge):
    result = subprocess.run([str(bridge), "--list"], capture_output=True, text=True, timeout=15, check=True)
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid bridge endpoint listing")
    return rows


def identity(rows):
    return sorted([{key: row.get(key) for key in ENDPOINT_KEYS} for row in rows], key=lambda row: json.dumps(row, sort_keys=True))


def select_device(rows, input_name, output_name):
    if input_name == output_name:
        raise ValueError("Input and output must be separate dedicated buses")
    chosen = []
    for name in (input_name, output_name):
        for direction in ("source", "destination"):
            matches = [row for row in rows if row.get("name") == name and row.get("direction") == direction]
            if len(matches) != 1:
                raise ValueError(f"Expected one {direction} named {name!r}; found {len(matches)}. Create dedicated IAC buses manually; installation never edits MIDI setup.")
            row = matches[0]
            if row.get("driver_owner") != DRIVER or not row.get("entity") or not row.get("entity_unique_id") or not row.get("device") or not row.get("unique_id") or not row.get("device_unique_id"):
                raise ValueError(f"{name!r} is not an identified Apple IAC device")
            chosen.append(row)
        if chosen[-1]["entity"] != chosen[-2]["entity"] or chosen[-1]["entity_unique_id"] != chosen[-2]["entity_unique_id"]:
            raise ValueError(f"The source and destination named {name!r} must belong to the same IAC bus")
    if chosen[0]["entity"] == chosen[2]["entity"] or chosen[0]["entity_unique_id"] == chosen[2]["entity_unique_id"] or len({row["unique_id"] for row in chosen}) != 4:
        raise ValueError("Dedicated buses must have distinct bus and endpoint identities")
    if len({row["device_unique_id"] for row in chosen}) != 1 or len({row["device"] for row in chosen}) != 1:
        raise ValueError("Dedicated buses must belong to the same IAC device")
    manufacturers = {normalized_component(row.get("manufacturer")) for row in chosen}
    models = {normalized_component(row.get("model")) for row in chosen}
    device_names = {normalized_component(row.get("device_name")) for row in chosen}
    if len(manufacturers) != 1 or len(models) != 1 or len(device_names) != 1:
        raise ValueError("Inconsistent IAC device metadata")
    return component(manufacturers.pop()), models.pop()


def lua_literal(value):
    return '"' + ''.join(f"\\{byte:03d}" for byte in value.encode("utf-8")) + '"'


def render(template, input_name, output_name, manufacturer, model,
           experimental_actions=False, experimental_mapped_parameter=False):
    if type(experimental_actions) is not bool or type(experimental_mapped_parameter) is not bool:
        raise ValueError("experimental flags must be booleans")
    text = Path(template).read_text()
    for key, value in (("INPUT", input_name), ("OUTPUT", output_name), ("MANUFACTURER", manufacturer), ("MODEL", model)):
        token = f"__MS_{key}__"
        if token not in text:
            raise ValueError(f"Profile template missing {token}")
        text = text.replace(token, lua_literal(value))
    for token, enabled in (("__MS_EXPERIMENTAL_ACTIONS__", experimental_actions),
                           ("__MS_EXPERIMENTAL_PARAMETER__", experimental_mapped_parameter)):
        if token not in text:
            raise ValueError(f"Profile template missing {token}")
        text = text.replace(token, "true" if enabled else "false")
    return text.encode("utf-8")


def read_manifest(state, root):
    state, root = safe_path(state), safe_path(root)
    if not state.exists():
        return None
    try:
        value = json.loads(state.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"Corrupt installation manifest {state.name} ({error}); remove {state.name} and the profile it describes, then reinstall") from error
    if not isinstance(value, dict):
        raise ValueError(f"Corrupt installation manifest {state.name}; remove {state.name} and the profile it describes, then reinstall")
    for field in ("profile_root", "file", "input", "output"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"Installation manifest field {field!r} must be a nonempty string")
    if type(value.get("version")) is not int:
        raise ValueError("Installation manifest field 'version' must be an integer")
    if not isinstance(value.get("endpoints"), list) or any(not isinstance(row, dict) or not set(ENDPOINT_KEYS) <= row.keys() for row in value["endpoints"]):
        raise ValueError("Installation manifest field 'endpoints' must be a list of endpoint objects with keys " + ", ".join(ENDPOINT_KEYS))
    if not isinstance(value.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", value["sha256"]):
        raise ValueError("Installation manifest field 'sha256' must be 64 lowercase hex characters")
    if value["version"] != 1 or value["profile_root"] != str(root):
        raise ValueError("Installation manifest version/root mismatch")
    target = safe_path(value["file"])
    if target.parent.parent.parent != root or target.name != "config.lua" or not target.parent.name.endswith(".device"):
        raise ValueError("Manifest points outside its owned profile")
    return value


@contextmanager
def locked(state):
    state = safe_path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    lock = safe_path(str(state) + ".lock")
    # Keep one inode so contenders cannot lock different files across an unlink/reopen race.
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OSError("Another install or uninstall is already running") from error
        yield
    finally:
        os.close(fd)


def conflicts(root, manufacturer, model, owned=None, device_names=()):
    found = []
    candidates = {canonical(name) for name in (model, *device_names)}
    for foreign, base in ((False, safe_path(root)), *((True, path) for path in SYSTEM_PROFILE_ROOTS)):
        if not base.exists():
            continue
        for maker in base.iterdir():
            if canonical(maker.name) != canonical(manufacturer):
                continue
            try:
                safe_path(maker)
                if not maker.is_dir():
                    found.append(str(maker))
                    continue
                for directory in maker.iterdir():
                    name = normalized_component(directory.name)
                    if not name.casefold().endswith(".device") or canonical(name[:-7]) not in candidates:
                        continue
                    safe_path(directory)
                    if owned is None or directory != owned.parent or not directory.is_dir():
                        found.append(str(directory))
                        continue
                    found.extend(str(path) for path in directory.rglob("*") if path != owned)
            except ValueError:
                # A symlinked system root is foreign territory we cannot inspect; skip it instead of aborting.
                if not foreign:
                    raise
    return sorted(found)


def install(bridge, input_name="MS Bridge Input", output_name="MS Bridge Output", profile_root=PROFILE_ROOT, state=STATE, template=None,
            experimental_actions=False, experimental_mapped_parameter=False):
    root, state = safe_path(profile_root), safe_path(state)
    with locked(state):
        before = endpoints(bridge)
        manufacturer, model = select_device(before, input_name, output_name)
        target = safe_path(root / manufacturer / (model + ".device") / "config.lua")
        old = read_manifest(state, root)
        if old and (old["file"] != str(target) or old["input"] != input_name or old["output"] != output_name
                    or bool(old.get("experimental_actions")) != experimental_actions
                    or old.get("experimental_mapped_parameter", False) != experimental_mapped_parameter):
            raise ValueError("Existing installation has different configuration; uninstall it first")
        device_names = {row["device_name"] for row in before if row.get("name") in (input_name, output_name)}
        orphan = False
        if old is None:
            template = template or files("mainstage_mcp").joinpath("profile.lua")
            data = render(template, input_name, output_name, manufacturer, model,
                          experimental_actions, experimental_mapped_parameter)
            # A crash between publishing config.lua and the manifest leaves our own bytes behind; adopt them.
            orphan = target.is_file() and target.read_bytes() == data
            if target.exists() and not orphan:
                raise ValueError("Conflicting profile file: " + str(target))
        conflicting = conflicts(root, manufacturer, model, target if (old or orphan) else None, device_names)
        if conflicting:
            raise ValueError("Conflicting profile files: " + ", ".join(conflicting))
        if old:
            if not target.is_file() or digest(target.read_bytes()) != old["sha256"]:
                raise ValueError("Owned profile was changed or removed; refusing overwrite")
            if identity(before) != old["endpoints"]:
                raise ValueError("MIDI endpoint identity changed since installation")
            return {"installed": True, "changed": False, "file": str(target),
                    "experimental_actions": experimental_actions,
                    "experimental_mapped_parameter": experimental_mapped_parameter}
        manifest = {"version": 1, "profile_root": str(root), "file": str(target), "sha256": digest(data), "input": input_name, "output": output_name,
                    "experimental_actions": experimental_actions,
                    "experimental_mapped_parameter": experimental_mapped_parameter,
                    "endpoints": identity(before), "bridge": str(Path(bridge).expanduser().resolve())}
        created = False
        state_created = False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not orphan:
                write_exclusive(target, data)
                created = True
            if identity(endpoints(bridge)) != identity(before):
                raise ValueError("MIDI identities changed during installation; profile rolled back")
            write_exclusive(state, (json.dumps(manifest, indent=2) + "\n").encode())
            state_created = True
        except BaseException:
            if state_created:
                state.unlink()
            if created and target.is_file() and digest(target.read_bytes()) == digest(data):
                target.unlink()
                try:
                    target.parent.rmdir()
                except OSError:
                    pass
            try:
                target.parent.parent.rmdir()
            except OSError:
                pass
            raise
        return {"installed": True, "changed": True, "file": str(target),
                "experimental_actions": experimental_actions,
                "experimental_mapped_parameter": experimental_mapped_parameter,
                "midi_configuration_changed": False}


def uninstall(state=STATE, profile_root=PROFILE_ROOT):
    state, root = safe_path(state), safe_path(profile_root)
    with locked(state):
        manifest = read_manifest(state, root)
        if not manifest:
            return {"uninstalled": True, "changed": False}
        target = safe_path(manifest["file"])
        if target.exists():
            if not target.is_file() or digest(target.read_bytes()) != manifest["sha256"]:
                return {"uninstalled": False, "preserved_changed_file": str(target)}
            target.unlink()
        state.unlink()
        for directory in (target.parent, target.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                pass
        return {"uninstalled": True, "changed": True}


def doctor(bridge, input_name="MS Bridge Input", output_name="MS Bridge Output", profile_root=PROFILE_ROOT, state=STATE):
    result = {"read_only": True, "runtime_handshake_tested": False, "mainstage_app_found": any(path.exists() for path in (Path("/Applications/MainStage.app"), Path.home() / "Applications/MainStage.app")), "issues": []}
    try:
        rows = endpoints(bridge)
        manufacturer, model = select_device(rows, input_name, output_name)
        result["buses_verified"] = True
        manifest = read_manifest(state, profile_root)
        if not manifest:
            result["issues"].append("Profile is not installed by this installer")
        else:
            target = safe_path(manifest["file"])
            if not target.is_file() or digest(target.read_bytes()) != manifest["sha256"]:
                result["issues"].append("Owned profile is missing or modified")
            if (manifest["input"], manifest["output"]) != (input_name, output_name):
                result["issues"].append("Requested buses differ from installed profile")
            if identity(rows) != manifest["endpoints"]:
                result["issues"].append("MIDI endpoint identities changed since installation")
        device_names = {row["device_name"] for row in rows if row.get("name") in (input_name, output_name)}
        result["issues"].extend("Conflicting profile: " + path for path in conflicts(profile_root, manufacturer, model, Path(manifest["file"]) if manifest else None, device_names))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        result["issues"].append(str(exc))
    if not result["mainstage_app_found"]:
        result["issues"].append("MainStage.app not found in standard Applications locations")
    result["static_checks_passed"] = not result["issues"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall", "doctor"))
    parser.add_argument("--bridge", type=Path)
    parser.add_argument("--input", default="MS Bridge Input")
    parser.add_argument("--output", default="MS Bridge Output")
    parser.add_argument("--profile-root", type=Path, default=PROFILE_ROOT)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--experimental-actions", action="store_true",
                        help="install live-unverified named action bindings")
    parser.add_argument("--experimental-mapped-parameter", action="store_true",
                        help="declare the unverified channel-16 CC90 mapped-parameter probe")
    args = parser.parse_args(argv)
    try:
        if args.command == "uninstall":
            result = uninstall(args.state, args.profile_root)
        else:
            if args.bridge is None:
                parser.error("--bridge is required for install and doctor")
            options = dict(bridge=args.bridge, input_name=args.input, output_name=args.output, profile_root=args.profile_root, state=args.state)
            result = install(**options, template=args.template,
                             experimental_actions=args.experimental_actions,
                             experimental_mapped_parameter=args.experimental_mapped_parameter) if args.command == "install" else doctor(**options)
        print(json.dumps(result, indent=2))
        return 1 if result.get("issues") or result.get("uninstalled") is False else 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
