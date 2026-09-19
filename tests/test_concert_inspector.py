import plistlib
import tempfile
import unittest
from pathlib import Path

from mainstage_mcp.concert_inspector import ConcertFormatError, inspect_concert


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_BINARY))


def engine(name: str, **values) -> dict:
    return {"name": name, "hasProgramChange": False, **values}


class ConcertInspectorTests(unittest.TestCase):
    def test_hierarchy_routes_and_strict_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Handcrafted.concert"
            write(root / "data.plist", {"Version": 57057})
            write(root / "Concert.patch/data.plist", {
                "VersionPatches": 40014,
                "channels": [{
                    "Channel_name": "Keys", "UUID": "synthetic-channel-1", "Channel_instID": 7,
                    "Channel_inputIsBus": False, "Channel_inputIndex_1": -1,
                    "Channel_outputIsBus": False, "Channel_outputIndex": 0,
                    "Channel_isMuted": False, "Channel_isSolo": False,
                    "Filename": "Keys.cst",
                }],
                "nodes": ["Set.patch"],
                "patch": {"engineNode": engine("Fixture")},
            })
            (root / "Concert.patch/Keys.cst").write_bytes(b"synthetic opaque setting")
            write(root / "Concert.patch/Set.patch/data.plist", {
                "VersionPatches": 40014, "channels": [], "nodes": ["Lead.patch"],
                "patch": {"engineNode": engine("Set")},
            })
            write(root / "Concert.patch/Set.patch/Lead.patch/data.plist", {
                "VersionPatches": 40014, "channels": [],
                "patch": {"engineNode": engine("Lead", hasProgramChange=True,
                                                 patchChangeNum=12, hasBankSelect=False)},
            })

            result = inspect_concert(root)
            self.assertEqual(result["concert"]["children"][0]["children"][0]["program_change"]["program"], 12)
            self.assertEqual(result["concert"]["channels"][0]["output"], {"is_bus": False, "index": 0})
            self.assertTrue(result["concert"]["channels"][0]["setting_present"])

            write(root / "data.plist", {"Version": 57058})
            with self.assertRaisesRegex(ConcertFormatError, "unsupported document Version"):
                inspect_concert(root)
            write(root / "data.plist", {"Version": 57057})
            patch_file = root / "Concert.patch/Set.patch/Lead.patch/data.plist"
            write(patch_file, {
                "VersionPatches": 40015, "channels": [],
                "patch": {"engineNode": engine("Lead")},
            })
            with self.assertRaisesRegex(ConcertFormatError, "unsupported VersionPatches"):
                inspect_concert(root)

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Handcrafted.concert"
            write(root / "data.plist", {"Version": 57057})
            write(root / "Concert.patch/data.plist", {
                "VersionPatches": 40014, "channels": [], "nodes": ["../Escape.patch"],
                "patch": {"engineNode": engine("Fixture")},
            })
            with self.assertRaisesRegex(ConcertFormatError, "unsafe node filename"):
                inspect_concert(root)


if __name__ == "__main__":
    unittest.main()
