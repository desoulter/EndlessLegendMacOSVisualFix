import io
import json
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest import mock

from elvisualfix.assets import AssetError, SerializedAsset, plan_rewrite, write_rewrite
from elvisualfix.installer import inspect, mutate, sha256
from elvisualfix import lz4
from elvisualfix.shaders import (PATH_SHADER, LAVA_SHADER, Shader, inspect_asset,
                                 repair_glsl, digest)
from tests.fixtures import (asset, shader, SOURCE, REPAIRED, UNRELATED_PROGRAM, RULES)


def rewrite(data, objects):
    parsed = SerializedAsset(data)
    plan = plan_rewrite(parsed, objects)
    output = io.BytesIO()
    write_rewrite(io.BytesIO(data), output, plan)
    return output.getvalue()


class CodecTests(unittest.TestCase):
    def test_literal_block_boundaries(self):
        rng = random.Random(2317)
        for size in (0, 1, 14, 15, 16, 269, 270, 271, 65536):
            data = bytes(rng.randrange(256) for _ in range(size))
            self.assertEqual(lz4.decompress(lz4.compress(data), size), data)

    def test_overlapping_match(self):
        # Three literals, then a back-reference of nine bytes at distance three.
        self.assertEqual(lz4.decompress(b"\x35abc\x03\x00\x00", 12), b"abcabcabcabc")

    def test_invalid_blocks(self):
        for data, size in ((b"\xf0", 20), (b"\x00\x00\x00", 4), (b"\x20x", 2),
                           (b"\x10x", 2), (b"\x10x\x02\x00", 5)):
            with self.assertRaises(ValueError):
                lz4.decompress(data, size)

    def test_specular_repair(self):
        repaired, count = repair_glsl(SOURCE)
        self.assertEqual(repaired, REPAIRED)
        self.assertEqual(count, 1)
        self.assertEqual(repair_glsl(REPAIRED), (REPAIRED, 0))


class AssetTests(unittest.TestCase):
    def test_order_ids_variant_counts_and_type_trees(self):
        for tree in (False, True):
            data = asset([(901, 28, b"DLC" * 59), (77, 48, shader(LAVA_SHADER, [SOURCE] * 3)),
                          (-8, 48, shader(PATH_SHADER, [SOURCE] * 2))], type_tree=tree, reverse_table=True)
            parsed = SerializedAsset(data)
            reports = inspect_asset(parsed, RULES)
            self.assertEqual({r.shader.name: r.changes for r in reports}, {PATH_SHADER: 2, LAVA_SHADER: 3})
            replacements = {r.obj.path_id: r.shader.rebuild(r.repaired_blob) for r in reports}
            modified = SerializedAsset(rewrite(data, replacements))
            self.assertEqual(modified.object_bytes(modified.by_id[901]), b"DLC" * 59)
            self.assertEqual([r.state for r in inspect_asset(modified, RULES)], ["installed"] * 2)

    def test_resize_grows_and_shrinks_without_changing_other_objects(self):
        data = asset(reverse_table=True)
        original = SerializedAsset(data)
        reports = inspect_asset(original, RULES)
        edits = {r.obj.path_id: r.shader.rebuild(r.repaired_blob) for r in reports}
        changed = rewrite(data, edits)
        self.assertNotEqual(len(data), len(changed))
        parsed = SerializedAsset(changed)
        for obj in original.objects:
            new = parsed.by_id[obj.path_id]
            self.assertEqual(new.start % 8, obj.start % 8)
            expected = edits.get(obj.path_id, original.object_bytes(obj))
            self.assertEqual(parsed.object_bytes(new), expected)
        self.assertTrue(changed.endswith(b"UNREFERENCED-TRAILER"))
        restored = rewrite(changed, {r.obj.path_id: r.shader.raw for r in reports})
        parsed_restore = SerializedAsset(restored)
        for obj in original.objects:
            self.assertEqual(parsed_restore.object_bytes(parsed_restore.by_id[obj.path_id]), original.object_bytes(obj))

    def test_lava_is_optional(self):
        reports = inspect_asset(SerializedAsset(asset([(12, 48, shader())])), RULES)
        self.assertEqual(len(reports), 1)

    def test_duplicate_shader_names_are_all_found(self):
        reports = inspect_asset(SerializedAsset(asset([(1, 48, shader()), (2, 48, shader())])), RULES)
        self.assertEqual(len(reports), 2)

    def test_unsupported_structures(self):
        good = asset()
        for position, replacement in ((8, struct.pack(">I", 17)), (16, b"\x01"),
                                      (4, struct.pack(">I", len(good) - 1))):
            data = good[:position] + replacement + good[position + len(replacement):]
            with self.assertRaises(AssetError):
                SerializedAsset(data)
        with self.assertRaises(AssetError):
            SerializedAsset(good[:20])

    def test_missing_path_shader(self):
        with self.assertRaises(AssetError):
            inspect_asset(SerializedAsset(asset([(2, 48, shader(LAVA_SHADER))])), RULES)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="visualfix-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resource = self.root / "resources.assets"
        self.state = self.root / "VisualFix"
        self.original = asset()
        self.resource.write_bytes(self.original)

    def install(self, **kwargs):
        return mutate(self.resource, self.state, "install", RULES, **kwargs)

    def uninstall(self):
        return mutate(self.resource, self.state, "uninstall", RULES)

    def test_install_idempotency_exact_rollback_and_repeat(self):
        original_hash = sha256(self.resource)
        result = self.install()
        self.assertTrue(result["changed"])
        self.assertEqual(sha256(result["backup"]), original_hash)
        self.assertTrue(all(r.state == "installed" for r in inspect(self.resource, RULES)[1]))
        identity = self.resource.stat().st_ino
        self.assertFalse(self.install()["changed"])
        self.assertEqual(self.resource.stat().st_ino, identity)
        result = self.uninstall()
        self.assertTrue(result["exact_restore"])
        self.assertEqual(self.resource.read_bytes(), self.original)
        self.assertFalse(self.uninstall()["changed"])
        self.assertTrue(self.install()["changed"])

    def test_dry_run_is_read_only(self):
        result = self.install(dry_run=True)
        self.assertEqual(result["planned"], 2)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.resource.read_bytes(), self.original)

    def test_different_file_content_does_not_require_a_whole_file_hash(self):
        self.resource.write_bytes(asset([(987, 28, b"A DIFFERENT DLC SET" * 200),
                                         (654, 48, shader()), (321, 48, shader(LAVA_SHADER))],
                                        reverse_table=True, padding=128))
        before = self.resource.read_bytes()
        self.assertTrue(self.install()["changed"])
        self.uninstall()
        self.assertEqual(self.resource.read_bytes(), before)

    def test_remove_after_dlc_moves_and_adds_assets(self):
        self.install()
        reports = inspect(self.resource, RULES)[1]
        by_name = {r.shader.name: r.shader.raw for r in reports}
        new_dlc = b"NEW DLC DATA THAT MUST BE PRESERVED" * 89
        self.resource.write_bytes(asset([(7000, 28, new_dlc),
            (550, 48, by_name[LAVA_SHADER]), (44, 48, by_name[PATH_SHADER])], reverse_table=True))
        result = self.uninstall()
        self.assertFalse(result["exact_restore"])
        parsed = SerializedAsset(self.resource.read_bytes())
        self.assertEqual(parsed.object_bytes(parsed.by_id[7000]), new_dlc)
        reports = inspect_asset(parsed, RULES)
        self.assertTrue(all(r.state == "needs-fix" for r in reports))
        self.assertEqual({r.shader.name: r.shader.raw for r in reports},
                         {r.shader.name: r.shader.raw for r in inspect_asset(SerializedAsset(self.original), RULES)})

    def test_unrelated_change_is_preserved_during_removal(self):
        self.install()
        parsed = SerializedAsset(self.resource.read_bytes())
        self.resource.write_bytes(rewrite(self.resource.read_bytes(), {301: b"NEW TEXTURE CONTENT"}))
        self.uninstall()
        parsed = SerializedAsset(self.resource.read_bytes())
        self.assertEqual(parsed.object_bytes(parsed.by_id[301]), b"NEW TEXTURE CONTENT")

    def test_pre_corrected_shader_is_preserved_and_not_owned(self):
        source = asset([(1, 48, shader(PATH_SHADER, [REPAIRED])), (2, 48, shader(LAVA_SHADER))])
        self.resource.write_bytes(source)
        self.install()
        reports = {r.shader.name: r for r in inspect(self.resource, RULES)[1]}
        self.assertEqual(reports[PATH_SHADER].state, "already-corrected")
        self.assertEqual(reports[LAVA_SHADER].state, "installed")
        self.uninstall()
        self.assertEqual(self.resource.read_bytes(), source)

    def test_stacked_installations_remove_all_owned_shaders(self):
        self.resource.write_bytes(asset([(1, 48, shader())]))
        self.install()
        path = inspect(self.resource, RULES)[1][0].shader.raw
        self.resource.write_bytes(asset([(99, 48, path), (2, 48, shader(LAVA_SHADER))]))
        self.install()
        result = self.uninstall()
        self.assertFalse(result["exact_restore"])
        self.assertTrue(all(r.state == "needs-fix" for r in inspect(self.resource, RULES)[1]))

    def test_unknown_shader_refused_before_resource_changes(self):
        self.resource.write_bytes(asset([(1, 48, shader(sources=[SOURCE.replace(b"1.0", b"2.0")]))]))
        before = self.resource.read_bytes()
        with self.assertRaises(AssetError):
            self.install()
        self.assertEqual(self.resource.read_bytes(), before)
        self.assertFalse((self.state / "backups").exists())

    def test_corrupt_backup_does_not_overwrite_game(self):
        result = self.install()
        result["backup"].write_bytes(b"DAMAGED")
        before = self.resource.read_bytes()
        with self.assertRaises(AssetError):
            self.uninstall()
        self.assertEqual(self.resource.read_bytes(), before)

    def test_missing_receipt_does_not_overwrite_game(self):
        self.install()
        for receipt in (self.state / "receipts").glob("*.json"):
            receipt.unlink()
        before = self.resource.read_bytes()
        with self.assertRaises(AssetError):
            self.uninstall()
        self.assertEqual(self.resource.read_bytes(), before)

    def test_partial_write_failure_leaves_original(self):
        with mock.patch("elvisualfix.installer.write_rewrite", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(self.resource.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob("resources.assets.visualfix-*")), [])

    def test_concurrent_update_is_not_overwritten(self):
        from elvisualfix import installer
        save = installer.save_backup
        other = self.original[:-1] + b"X"
        def change_file(*args):
            backup = save(*args)
            self.resource.write_bytes(other)
            return backup
        with mock.patch("elvisualfix.installer.save_backup", side_effect=change_file):
            with self.assertRaises(AssetError):
                self.install()
        self.assertEqual(self.resource.read_bytes(), other)


if __name__ == "__main__":
    unittest.main()
