"""Installer checks use isolated directories and fake read-only bridge output."""
import copy
import io
import json
import multiprocessing
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mainstage_mcp import installation as setup


def hold_lock(state, connection):
    try:
        with setup.locked(Path(state)):
            connection.send(None)
            connection.recv()
    except BaseException as error:
        connection.send(repr(error))
    finally:
        connection.close()


def crash_install(options, step):
    """Install in a child that dies like a killed process at `step`, leaving that step's disk state behind."""
    target = Path(options["profile_root"]) / "Apple Inc/Sterownik IAC.device/config.lua"
    write, link, unlink, make = setup.write_exclusive, os.link, os.unlink, Path.mkdir

    def mkdir_or_die(after):
        def mkdir(self, *args, **kwargs):
            if self == target.parent and not after:
                os._exit(17)
            make(self, *args, **kwargs)
            if self == target.parent:
                os._exit(17)
        return mkdir

    def die_writing(path):
        def write_or_die(destination, data):
            if destination == path:
                os._exit(17)
            write(destination, data)
        return write_or_die

    def link_or_die(source, destination):  # our temporary file is complete but not yet published
        if Path(destination) == target:
            os._exit(17)
        link(source, destination)

    def unlink_or_die(path):  # config.lua is published beside our not yet removed temporary file
        if Path(path).parent == target.parent:
            os._exit(17)
        unlink(path)

    def unlink_journal_or_die(path):  # the manifest is published, its journal not yet removed
        if Path(path) == Path(str(options["state"]) + ".pending"):
            os._exit(17)
        unlink(path)

    module, name, replacement = {"journaled": (Path, "mkdir", mkdir_or_die(after=False)),
                                 "directory": (Path, "mkdir", mkdir_or_die(after=True)),
                                 "temporary": (os, "link", link_or_die),
                                 "published": (os, "unlink", unlink_or_die),
                                 "manifest": (setup, "write_exclusive", die_writing(Path(options["state"]))),
                                 "committed": (os, "unlink", unlink_journal_or_die)}[step]
    with patch.object(setup, "endpoints", return_value=rows()), patch.object(module, name, replacement):
        setup.install(**options)


def rows():
    return [dict(direction=direction, name=name, display_name=name, unique_id=index * 2 + offset + 1,
                 entity=index + 10, entity_unique_id=index + 20, device=99, device_unique_id=100,
                 device_name="Sterownik IAC", manufacturer="Apple Inc.", model="Sterownik IAC",
                 driver_owner=setup.DRIVER)
            for index, name in enumerate(("MS Bridge Input", "MS Bridge Output"))
            for offset, direction in enumerate(("source", "destination"))]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "profiles"
        self.state = self.base / "state/installation.json"
        self.template = self.base / "template.lua"
        self.template.write_text("\n".join(
            ["local " + key + " = __MS_" + key + "__" for key in ("INPUT", "OUTPUT", "MANUFACTURER", "MODEL")]
            + ["local actions = __MS_EXPERIMENTAL_ACTIONS__",
               "local parameter = __MS_EXPERIMENTAL_PARAMETER__"]))
        self.options = dict(bridge=self.base / "bridge", profile_root=self.root, state=self.state,
                            template=self.template)
        self.listing = patch.object(setup, "endpoints", return_value=rows()).start()
        self.addCleanup(patch.stopall)

    def reset(self):
        for path in (self.root, self.state.parent):
            shutil.rmtree(path, ignore_errors=True)

    def test_roundtrip_idempotence_and_metadata(self):
        result = setup.install(**self.options)
        target = Path(result["file"])
        self.assertEqual(target.parent.parent.name, "Apple Inc")
        self.assertEqual(target.parent.name, "Sterownik IAC.device")
        self.assertNotIn("__MS_", target.read_text())
        self.assertFalse(setup.install(**self.options)["changed"])
        extra = target.parent / "user-notes.txt"
        extra.write_text("keep")
        self.assertTrue(setup.uninstall(self.state, self.root)["uninstalled"])
        self.assertEqual(extra.read_text(), "keep")
        self.assertFalse(setup.uninstall(self.state, self.root)["changed"])

    def test_experimental_actions_are_explicit_opt_in(self):
        target = Path(setup.install(**self.options, experimental_actions=True)["file"])
        self.assertIn("local actions = true", target.read_text())
        manifest = json.loads(self.state.read_text())
        self.assertTrue(manifest["experimental_actions"])
        with self.assertRaisesRegex(ValueError, "different configuration"):
            setup.install(**self.options)

        setup.uninstall(self.state, self.root)
        target = Path(setup.install(**self.options)["file"])
        self.assertIn("local actions = false", target.read_text())

    def test_existing_conflict_is_untouched(self):
        target = self.root / "Apple Inc" / "Sterownik IAC.device" / "config.lua"
        target.parent.mkdir(parents=True)
        target.write_text("original")
        with self.assertRaisesRegex(ValueError, r"Conflicting profile file: .+config\.lua"):
            setup.install(**self.options)
        self.assertEqual(target.read_text(), "original")
        self.assertFalse(self.state.exists())

    def test_changed_owned_file_is_preserved(self):
        target = Path(setup.install(**self.options)["file"])
        target.write_text("user change")
        self.assertFalse(setup.uninstall(self.state, self.root)["uninstalled"])
        self.assertTrue(self.state.exists())
        with self.assertRaisesRegex(ValueError, "changed"):
            setup.install(**self.options)
        self.assertEqual(target.read_text(), "user change")

    def test_profile_aliases_are_conflicts_even_when_empty(self):
        for maker, model in (("apple inc", "Sterownik IAC"), ("Apple Inc ", "Sterownik IAC"),
                             ("Apple Inc", "Sterownik IAC."), ("Apple Inc. . ", "sterownik iac "),
                             ("Apple Inc", "Renamed IAC")):
            for empty in (True, False):
                with self.subTest(maker=maker, model=model, empty=empty):
                    listing = rows()
                    for row in listing:
                        row["device_name"] = "Renamed IAC"
                    self.listing.return_value = listing
                    directory = self.root / maker / (model + ".device")
                    directory.mkdir(parents=True)
                    if not empty:
                        (directory / "user-owned.lua").write_text("keep")
                    with self.assertRaisesRegex(ValueError, "Conflicting"):
                        setup.install(**self.options)
                    self.assertFalse(self.state.exists())
                    self.assertFalse(list(self.root.rglob("config.lua")))
                    if not empty:
                        self.assertEqual((directory / "user-owned.lua").read_text(), "keep")
                    shutil.rmtree(self.root)

    def test_metadata_normalization_applies_to_install_destination(self):
        listing = rows()
        for row in listing:
            row["manufacturer"] = "Apple Inc. "
            row["model"] = "Sterownik IAC."
        self.listing.return_value = listing
        target = Path(setup.install(**self.options)["file"])
        self.assertEqual(target, self.root / "Apple Inc/Sterownik IAC.device/config.lua")

    def test_doctor_reports_device_name_alias_after_install(self):
        listing = rows()
        for row in listing:
            row["device_name"] = "Renamed IAC"
        self.listing.return_value = listing
        setup.install(**self.options)
        alias = self.root / "Apple Inc/Renamed IAC.device"
        alias.mkdir()
        options = {key: value for key, value in self.options.items() if key != "template"}
        self.assertIn("Conflicting profile: " + str(alias), setup.doctor(**options)["issues"])

    def test_identity_change_rolls_back(self):
        changed = rows()
        changed[0]["unique_id"] = 888
        self.listing.side_effect = [rows(), changed]
        with self.assertRaisesRegex(ValueError, "rolled back"):
            setup.install(**self.options)
        self.assertFalse(list(self.root.rglob("config.lua")))
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_identity_change_after_install_is_reported(self):
        setup.install(**self.options)
        changed = rows()
        changed[0]["unique_id"] = 888
        self.listing.return_value = changed
        options = {k: v for k, v in self.options.items() if k != "template"}
        result = setup.doctor(**options)
        self.assertIn("MIDI endpoint identities changed since installation", result["issues"])
        self.assertFalse(result["runtime_handshake_tested"])

    def test_manifest_write_failure_rolls_back_profile(self):
        write = setup.write_exclusive
        def fail_manifest(path, data):
            if path == self.state:
                raise OSError("disk full")
            write(path, data)
        with patch.object(setup, "write_exclusive", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "disk full"):
                setup.install(**self.options)
        self.assertFalse(list(self.root.rglob("config.lua")))
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_profile_write_failure_leaves_no_directory_behind(self):
        write = setup.write_exclusive

        def fail_profile(path, data):
            if path.name == "config.lua":
                raise OSError("disk full")
            write(path, data)
        with patch.object(setup, "write_exclusive", side_effect=fail_profile):
            with self.assertRaisesRegex(OSError, "disk full"):
                setup.install(**self.options)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual([path.name for path in self.state.parent.iterdir()], ["installation.json.lock"])
        self.assertTrue(setup.install(**self.options)["changed"])

    def test_profile_directory_appearing_mid_install_is_not_taken_over(self):
        directory = self.root / "Apple Inc/Sterownik IAC.device"
        scan = setup.conflicts

        def scan_then_race(*args):
            found = scan(*args)
            directory.mkdir(parents=True)  # someone else creates it between the scan and our mkdir
            return found
        with patch.object(setup, "conflicts", side_effect=scan_then_race):
            with self.assertRaises(FileExistsError):
                setup.install(**self.options)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertFalse(self.state.exists())
        self.assertEqual([path.name for path in self.state.parent.iterdir()], ["installation.json.lock"])

    def test_crash_orphan_with_identical_bytes_is_adopted(self):
        manufacturer, model = setup.select_device(rows(), "MS Bridge Input", "MS Bridge Output")
        target = self.root / manufacturer / (model + ".device") / "config.lua"
        target.parent.mkdir(parents=True)
        data = setup.render(self.template, "MS Bridge Input", "MS Bridge Output", manufacturer, model)
        target.write_bytes(data)
        setup.install(**self.options)
        manifest = json.loads(self.state.read_text())
        self.assertEqual(manifest["file"], str(target))
        self.assertEqual(manifest["sha256"], setup.digest(data))
        self.assertFalse(setup.install(**self.options)["changed"])

    def test_crash_orphan_with_foreign_bytes_names_config(self):
        target = self.root / "Apple Inc" / "Sterownik IAC.device" / "config.lua"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"someone else")
        with self.assertRaisesRegex(ValueError, r"config\.lua"):
            setup.install(**self.options)
        self.assertEqual(target.read_bytes(), b"someone else")
        self.assertFalse(self.state.exists())

    def test_corrupt_manifest_error_names_file_for_both_commands(self):
        self.state.parent.mkdir(parents=True)
        for content in (b"{not json", b'{"file": "\xff"}'):
            self.state.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "installation.json.*remove installation.json"):
                setup.uninstall(self.state, self.root)
            for command, argv in (
                    ("install", ["install", "--bridge", str(self.options["bridge"]), "--profile-root", str(self.root),
                                 "--state", str(self.state), "--template", str(self.template)]),
                    ("uninstall", ["uninstall", "--profile-root", str(self.root), "--state", str(self.state)])):
                with self.subTest(command=command, content=content):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        self.assertEqual(setup.main(argv), 1)
                    self.assertIn("installation.json", buffer.getvalue())
                    self.assertIn("remove", buffer.getvalue())

    def test_manifest_schema_violations_name_the_field_and_remedy(self):
        target = Path(setup.install(**self.options)["file"])
        good = json.loads(self.state.read_text())
        cases = [(field, {key: value for key, value in good.items() if key != field})
                 for field in ("version", "profile_root", "file", "input", "output", "endpoints", "sha256")]
        cases += [("input", {**good, "input": "  "}), ("sha256", {**good, "sha256": good["sha256"].upper()}),
                  ("endpoints", {**good, "endpoints": ["source"]}), ("endpoints", {**good, "endpoints": {}}),
                  ("version", {**good, "version": True}), ("installation.json", "{")]
        for case, (expected, manifest) in enumerate(cases):
            with self.subTest(case=case, expected=expected):
                text = manifest if isinstance(manifest, str) else json.dumps(manifest)
                self.state.write_text(text)
                with self.assertRaisesRegex(ValueError, expected + ".*remove installation.json .*reinstall"):
                    setup.uninstall(self.state, self.root)
                # A manifest this installer never wrote proves nothing, so nothing is deleted on its word.
                self.assertTrue(target.is_file())
                self.assertEqual(self.state.read_text(), text)

    def test_endpoint_keys_added_or_dropped_later_keep_manifest_usable(self):
        target = Path(setup.install(**self.options)["file"])
        manifest = json.loads(self.state.read_text())
        for row in manifest["endpoints"]:
            row["retired_key"] = "recorded by another version"
        self.state.write_text(json.dumps(manifest))
        options = {key: value for key, value in self.options.items() if key != "template"}
        self.assertFalse(setup.install(**self.options)["changed"])
        self.assertNotIn("MIDI endpoint identities changed since installation", setup.doctor(**options)["issues"])
        with patch.object(setup, "ENDPOINT_KEYS", setup.ENDPOINT_KEYS + ("future_key",)):
            # Unrecorded keys compare as None, like keys the bridge omits: equal until the bridge reports a value.
            self.assertFalse(setup.install(**self.options)["changed"])
            listing = rows()
            for row in listing:
                row["future_key"] = 7
            self.listing.return_value = listing
            with self.assertRaisesRegex(ValueError, "identity changed"):
                setup.install(**self.options)
            self.assertIn("MIDI endpoint identities changed since installation", setup.doctor(**options)["issues"])
            self.assertTrue(setup.uninstall(self.state, self.root)["changed"])
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_crash_residue_is_rolled_back_by_install_or_uninstall(self):
        context = multiprocessing.get_context("spawn")
        directory = self.root / "Apple Inc/Sterownik IAC.device"
        options = {key: value for key, value in self.options.items() if key != "template"}
        for step, residue in (("journaled", None), ("directory", []), ("temporary", ["<temporary>"]),
                              ("published", ["<temporary>", "config.lua"]), ("manifest", ["config.lua"])):
            for recovery in ("install", "uninstall"):
                with self.subTest(step=step, recovery=recovery):
                    self.reset()
                    child = context.Process(target=crash_install, args=(self.options, step))
                    child.start()
                    child.join(60)
                    self.assertEqual(child.exitcode, 17)
                    self.assertFalse(self.state.exists())
                    self.assertEqual(directory.exists() and sorted(
                        re.sub(r"^\.mainstage-mcp-.*", "<temporary>", path.name) for path in directory.iterdir()),
                                     residue if residue is not None else False)
                    issues = setup.doctor(**options)["issues"]
                    if recovery == "install":
                        self.assertTrue(setup.install(**self.options)["changed"])
                        self.assertEqual([path.name for path in directory.iterdir()], ["config.lua"])
                        self.assertEqual(json.loads(self.state.read_text())["sha256"],
                                         setup.digest((directory / "config.lua").read_bytes()))
                        self.assertFalse(setup.install(**self.options)["changed"])
                    self.assertTrue(setup.uninstall(self.state, self.root)["changed"])
                    self.assertFalse(self.root.exists() and list(self.root.iterdir()))
                    self.assertEqual([path.name for path in self.state.parent.iterdir()], ["installation.json.lock"])
                    self.assertTrue(any("interrupted" in issue for issue in issues), issues)

    def test_crash_after_manifest_publish_leaves_working_installation(self):
        journal = Path(str(self.state) + ".pending")
        options = {key: value for key, value in self.options.items() if key != "template"}
        for command in ("install", "uninstall"):
            with self.subTest(command=command):
                self.reset()
                child = multiprocessing.get_context("spawn").Process(target=crash_install,
                                                                     args=(self.options, "committed"))
                child.start()
                child.join(60)
                self.assertEqual(child.exitcode, 17)
                self.assertTrue(self.state.exists() and journal.exists())
                self.assertFalse(any("interrupted" in issue for issue in setup.doctor(**options)["issues"]))
                if command == "install":
                    self.assertFalse(setup.install(**self.options)["changed"])
                    self.assertFalse(journal.exists())
                self.assertTrue(setup.uninstall(self.state, self.root)["changed"])
                self.assertEqual(list(self.root.iterdir()), [])
                self.assertEqual([path.name for path in self.state.parent.iterdir()], ["installation.json.lock"])

    def test_crash_recovery_leaves_foreign_files_alone(self):
        directory = self.root / "Apple Inc/Sterownik IAC.device"
        outside = self.base / "outside.txt"
        outside.write_text("keep")
        foreign = [directory / "notes.txt", directory / ".mainstage-mcp-note", directory / ".mainstage-mcp-ABCDEFGH",
                   directory / ".mainstage-mcp-abcdefgh.lua", directory.parent / ".mainstage-mcp-abcdefgh",
                   self.root / "Apple Inc/Sterownik IAC..device/.mainstage-mcp-abcdefgh"]
        kept = {".mainstage-mcp-linkxxxx", ".mainstage-mcp-dirxxxxx", *(path.name for path in foreign[:4])}
        for command in ("install", "uninstall"):
            with self.subTest(command=command):
                self.reset()
                child = multiprocessing.get_context("spawn").Process(target=crash_install,
                                                                     args=(self.options, "published"))
                child.start()
                child.join(60)
                self.assertEqual(child.exitcode, 17)
                self.assertIn("config.lua", [path.name for path in directory.iterdir()])
                for path in foreign:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"foreign")
                (directory / ".mainstage-mcp-linkxxxx").symlink_to(outside)
                (directory / ".mainstage-mcp-dirxxxxx").mkdir()
                if command == "install":
                    with self.assertRaisesRegex(ValueError, "Conflicting"):
                        setup.install(**self.options)
                else:
                    self.assertTrue(setup.uninstall(self.state, self.root)["changed"])
                # Only our hash-matched config.lua and exactly named temporary file were removed.
                self.assertEqual({path.name for path in directory.iterdir()}, kept)
                self.assertTrue(all(path.read_bytes() == b"foreign" for path in foreign))
                self.assertTrue((directory / ".mainstage-mcp-linkxxxx").is_symlink())
                self.assertTrue((directory / ".mainstage-mcp-dirxxxxx").is_dir())
                self.assertEqual(outside.read_text(), "keep")
                self.assertFalse(self.state.exists())
                self.assertFalse(setup.uninstall(self.state, self.root)["changed"])

    def test_unproven_empty_profile_directory_gets_actionable_error(self):
        directory = self.root / "Apple Inc/Sterownik IAC.device"
        for names in ((), (".mainstage-mcp-abcdefgh",)):
            with self.subTest(names=names):
                self.reset()
                directory.mkdir(parents=True)
                for name in names:
                    (directory / name).write_bytes(b"partial")
                with self.assertRaisesRegex(ValueError, r"Sterownik IAC\.device.*remove it if you did not create it"):
                    setup.install(**self.options)
                self.assertFalse(setup.uninstall(self.state, self.root)["changed"])
                self.assertEqual(sorted(path.name for path in directory.iterdir()), list(names))
                self.assertFalse(self.state.exists())

    def test_manifest_for_another_root_or_version_names_the_way_out(self):
        setup.install(**self.options)
        other = self.base / "other-profiles"
        with self.assertRaisesRegex(ValueError, "installation.json is for profile root " + re.escape(str(self.root))
                                    + "; pass that path as --profile-root"):
            setup.uninstall(self.state, other)
        manifest = json.loads(self.state.read_text())
        self.state.write_text(json.dumps({**manifest, "version": 2}))
        with self.assertRaisesRegex(ValueError, "installation.json has version 2; use the mainstage-mcp release"):
            setup.uninstall(self.state, self.root)

    def test_lock_file_permissions_are_tightened(self):
        self.state.parent.mkdir(parents=True)
        lock = Path(str(self.state) + ".lock")
        lock.touch(mode=0o644)
        with setup.locked(self.state):
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_symlinked_system_profile_root_is_skipped(self):
        real = self.base / "system-profiles"
        (real / "Apple Inc." / "Sterownik IAC.device").mkdir(parents=True)
        linked = self.base / "linked-system-profiles"
        linked.symlink_to(real, target_is_directory=True)
        target = self.root / "Apple Inc" / "Sterownik IAC.device" / "config.lua"
        with patch.object(setup, "SYSTEM_PROFILE_ROOTS", (linked,)):
            self.assertEqual(setup.conflicts(self.root, "Apple Inc.", "Sterownik IAC"), [])
            self.assertTrue(setup.install(**self.options)["installed"])
        with patch.object(setup, "SYSTEM_PROFILE_ROOTS", (real,)):
            self.assertEqual(setup.conflicts(self.root, "Apple Inc.", "Sterownik IAC", target),
                             [str(real / "Apple Inc." / "Sterownik IAC.device")])
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                setup.install(**self.options)

    def test_only_a_symlinked_system_root_is_skipped(self):
        elsewhere = self.base / "elsewhere/Sterownik IAC.device"
        elsewhere.mkdir(parents=True)
        real = self.base / "system-profiles"
        for link, source in (("Apple Inc.", elsewhere.parent), ("Apple Inc./Sterownik IAC.device", elsewhere)):
            with self.subTest(link=link):
                self.reset()
                shutil.rmtree(real, ignore_errors=True)
                (real / link).parent.mkdir(parents=True, exist_ok=True)
                (real / link).symlink_to(source, target_is_directory=True)
                with patch.object(setup, "SYSTEM_PROFILE_ROOTS", (real,)):
                    with self.assertRaisesRegex(ValueError, "symbolic link: " + re.escape(str(real / link))):
                        setup.install(**self.options)
                self.assertFalse(self.state.exists())
                self.assertFalse(list(self.root.rglob("config.lua")))

    def test_unnormalizable_system_entry_names_are_skipped_but_not_in_user_root(self):
        real = self.base / "system-profiles"
        maker = real / "Apple Inc."
        maker.mkdir(parents=True)
        (maker / "Icon\r").write_bytes(b"")
        (maker / "...").mkdir()
        with patch.object(setup, "SYSTEM_PROFILE_ROOTS", (real,)):
            self.assertEqual(setup.conflicts(self.root, "Apple Inc.", "Sterownik IAC"), [])
            (maker / "Sterownik IAC.device").mkdir()
            self.assertEqual(setup.conflicts(self.root, "Apple Inc.", "Sterownik IAC"),
                             [str(maker / "Sterownik IAC.device")])
            (self.root / "Apple Inc").mkdir(parents=True)
            (self.root / "Apple Inc/Icon\r").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                setup.conflicts(self.root, "Apple Inc.", "Sterownik IAC")

    def test_wrong_driver_duplicate_or_different_device(self):
        for field, value in (("driver_owner", "untrusted"), ("device_unique_id", 999), ("entity", 0),
                             ("entity", 888), ("entity_unique_id", 999), ("entity_unique_id", 0)):
            candidate = rows()
            candidate[0][field] = value
            with self.assertRaises(ValueError):
                setup.select_device(candidate, "MS Bridge Input", "MS Bridge Output")
        with self.assertRaises(ValueError):
            setup.select_device(rows() + [copy.copy(rows()[0])], "MS Bridge Input", "MS Bridge Output")

    def test_symlinks_and_manifest_traversal_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            setup.install(**self.options)
        self.root.unlink()
        setup.install(**self.options)
        manifest = json.loads(self.state.read_text())
        manifest["file"] = str(outside / "config.lua")
        self.state.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "outside"):
            setup.uninstall(self.state, self.root)

    def test_lua_literal_encodes_quotes_newlines_and_utf8(self):
        self.assertEqual(setup.lua_literal('"\nż'), '"\\034\\010\\197\\188"')

    def test_experimental_parameter_is_explicit_and_part_of_configuration(self):
        rendered = setup.render(self.template, "in", "out", "maker", "model").decode()
        self.assertIn("local parameter = false", rendered)
        result = setup.install(**self.options, experimental_mapped_parameter=True)
        self.assertIn("local parameter = true", Path(result["file"]).read_text())
        manifest = json.loads(self.state.read_text())
        self.assertIs(manifest["experimental_mapped_parameter"], True)
        with self.assertRaisesRegex(ValueError, "different configuration"):
            setup.install(**self.options)

    def test_render_requires_both_experimental_tokens(self):
        self.template.write_text(self.template.read_text().replace("__MS_EXPERIMENTAL_PARAMETER__", "false"))
        with self.assertRaisesRegex(ValueError, "missing __MS_EXPERIMENTAL_PARAMETER__"):
            setup.render(self.template, "in", "out", "maker", "model")

    def test_lock_survives_process_death_without_staying_owned(self):
        lock = Path(str(self.state) + ".lock")
        lock.parent.mkdir(parents=True)
        lock.touch(mode=0o600)
        inode = lock.stat().st_ino
        with setup.locked(self.state):
            self.assertEqual(lock.stat().st_ino, inode)

        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        holder = context.Process(target=hold_lock, args=(self.state, child))
        try:
            holder.start()
            child.close()
            self.assertTrue(parent.poll(5), "lock holder did not start")
            self.assertIsNone(parent.recv())
            with self.assertRaisesRegex(OSError, "already running"):
                with setup.locked(self.state):
                    self.fail("acquired a lock owned by another process")
            self.assertEqual(lock.stat().st_ino, inode)
            holder.terminate()
            holder.join(5)
            self.assertFalse(holder.is_alive(), "lock holder did not terminate")
            with setup.locked(self.state):
                self.assertEqual(lock.stat().st_ino, inode)
            self.assertTrue(lock.exists())
            self.assertEqual(lock.stat().st_ino, inode)
        finally:
            if holder.is_alive():
                holder.kill()
                holder.join(5)
            parent.close()
            child.close()

    def test_lock_symlink_is_rejected(self):
        lock = Path(str(self.state) + ".lock")
        lock.parent.mkdir(parents=True)
        lock.symlink_to(self.base / "elsewhere")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            with setup.locked(self.state):
                pass


if __name__ == "__main__":
    unittest.main()
