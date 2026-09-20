"""Installer checks use isolated directories and fake read-only bridge output."""
import copy
import json
import multiprocessing
from pathlib import Path
import shutil
import tempfile
import unittest
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
        self.options = dict(bridge=self.base / "bridge", profile_root=self.root, state=self.state, template=self.template)
        self.listing = patch.object(setup, "endpoints", return_value=rows()).start()
        self.addCleanup(patch.stopall)

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
        target = self.root / "Apple Inc." / "Sterownik IAC.device" / "config.lua"
        target.parent.mkdir(parents=True)
        target.write_text("original")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
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
