import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
from PIL import Image
from fastapi.testclient import TestClient

folder = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = folder.name
for role in ("admin", "member"):
    os.environ[role.upper() + "_USER"] = role
    salt = b"test-salt-for-tests"
    digest = hashlib.pbkdf2_hmac("sha256", b"test-password", salt, 260000).hex()
    os.environ[role.upper() + "_HASH"] = salt.hex() + ":" + digest

from backend.app import app
from backend.app import minecraft_face, composite_avatars, database, migrate_avatar_layers


def png(color, size):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.admin = TestClient(app)
        self.member = TestClient(app)
        for role, client in (("admin", self.admin), ("member", self.member)):
            self.assertEqual(client.post("/api/login", json={"username": role, "password": "test-password"}).status_code, 200)
        response = self.admin.post("/api/photos", files={"file": ("合照.png", png("white", (800, 600)), "image/png")})
        self.assertEqual(response.status_code, 200)
        self.photo = response.json()
        self.path = "/api/photos/" + self.photo["id"]

    def tearDown(self):
        self.admin.delete(self.path)

    def add(self, name, color="red"):
        response = self.member.post(self.path + "/avatars", data={"mode": "upload", "username": name},
                                    files={"file": ("head.png", png(color, (8, 8)), "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_permissions_and_private_images(self):
        guest = TestClient(app)
        for path in ("/api/photos", self.path, self.path + "/image", self.path + "/export", self.path + "/thumbnail"):
            self.assertEqual(guest.get(path).status_code, 401)
        self.assertEqual(self.member.post("/api/photos", files={"file": ("x.png", png("red", (8, 8)))}).status_code, 403)
        self.assertEqual(self.member.delete(self.path).status_code, 403)
        avatar = self.add("Steve")["selected"]
        self.assertEqual(guest.get("/api/avatars/" + avatar + "/image").status_code, 401)
        self.assertEqual(guest.put(self.path + "/layers", json={"version": 0, "avatar_ids": []}).status_code, 401)
        self.assertEqual(self.member.post("/api/logout", headers={"origin": "https://foreign.example"}).status_code, 403)

    def test_conflicts_defaults_and_export(self):
        first = self.add("first")["photo"]["avatars"][0]
        request = {**first, "size": .12, "x": .25, "rotation": 90}
        result = self.member.patch(self.path + "/avatars/" + first["id"], json=request)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.member.patch(self.path + "/avatars/" + first["id"], json=request).status_code, 409)
        added = self.add("second")["photo"]
        self.assertAlmostEqual(added["avatars"][1]["size"], .12)
        self.assertEqual(self.member.post(self.path + "/uniform", json={"version": 0, "size": .15}).status_code, 409)
        result = self.member.post(self.path + "/uniform", json={"version": added["version"], "size": .15})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(all(a["size"] == .15 for a in result.json()["avatars"]))
        output = self.member.get(self.path + "/export")
        self.assertEqual(output.status_code, 200)
        image = Image.open(io.BytesIO(output.content))
        self.assertEqual(image.size, (800, 600))
        self.assertEqual(image.getpixel((200, 300)), (255, 0, 0))
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))

    def test_parallel_edits_to_different_avatars(self):
        self.add("one")
        photo = self.add("two")["photo"]
        def save(avatar):
            return self.member.patch(self.path + "/avatars/" + avatar["id"], json={**avatar, "x": .1}).status_code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, photo["avatars"]))
        self.assertEqual(results, [200, 200])
        self.assertTrue(all(a["x"] == .1 for a in self.member.get(self.path).json()["avatars"]))

    def test_invalid_upload_and_duplicate(self):
        self.assertEqual(self.admin.post("/api/photos", files={"file": ("bad.png", b"bad")}).status_code, 400)
        self.add("Name")
        response = self.member.post(self.path + "/avatars", data={"mode": "upload", "username": "name"},
                                    files={"file": ("head.png", png("red", (8, 8)), "image/png")})
        self.assertEqual(response.status_code, 409)

    def test_default_skin_account(self):
        profile, session, avatar = MagicMock(), MagicMock(), MagicMock()
        profile.json.return_value = {"id": "069a79f444e94726a5befca90e38aaf5", "name": "DefaultPlayer"}
        session.json.return_value = {"properties": []}
        avatar.content = png("blue", (8, 8))
        with patch("backend.app.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.side_effect = [profile, session, avatar]
            name, image = minecraft_face("DefaultPlayer")
        self.assertEqual(name, "DefaultPlayer")
        self.assertEqual(image.size, (8, 8))

    def test_thumbnail_tracks_edits_and_reuses_cache(self):
        avatar = self.add("ThumbnailPlayer")["photo"]["avatars"][0]
        with patch("backend.app.composite_avatars", wraps=composite_avatars) as render:
            response = self.member.get(self.path + "/thumbnail?v=0")
            self.assertEqual(response.status_code, 200)
            image = Image.open(io.BytesIO(response.content))
            self.assertEqual(image.size, (640, 480))
            self.assertGreater(image.getpixel((320, 240))[0], 220)
            self.assertLess(image.getpixel((320, 240))[1], 30)
            self.assertEqual(self.member.get(self.path + "/thumbnail").content, response.content)
            self.assertEqual(render.call_count, 1)

            moved = self.member.patch(self.path + "/avatars/" + avatar["id"],
                                      json={**avatar, "x": .25, "rotation": 35}).json()
            updated = Image.open(io.BytesIO(self.member.get(self.path + "/thumbnail").content))
            self.assertGreater(min(updated.getpixel((320, 240))), 240)
            self.assertLess(updated.getpixel((160, 240))[1], 30)
            self.assertEqual(render.call_count, 2)

            self.member.post(self.path + "/uniform", json={"version": moved["version"], "size": .2}).raise_for_status()
            self.member.get(self.path + "/thumbnail").raise_for_status()
            self.assertEqual(render.call_count, 3)
            original = Image.open(io.BytesIO(self.member.get(self.path + "/image?preview=true").content))
            self.assertGreater(min(original.getpixel((200, 300))), 240)

            latest = self.member.get(self.path).json()["avatars"][0]
            self.member.delete(self.path + "/avatars/" + latest["id"] + "?version=" + str(latest["version"])).raise_for_status()
            empty = Image.open(io.BytesIO(self.member.get(self.path + "/thumbnail").content))
            self.assertGreater(min(empty.getpixel((160, 240))), 240)
            self.assertEqual(render.call_count, 4)

        self.admin.delete(self.path).raise_for_status()
        with database() as db:
            count = db.execute("SELECT COUNT(*) FROM photo_thumbnails WHERE photo_id=?", (self.photo["id"],)).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(self.member.get(self.path + "/thumbnail").status_code, 404)

    def test_layer_order_persists_in_export_thumbnail_and_transforms(self):
        first = self.add("front", "red")["photo"]["avatars"][0]
        photo = self.add("back", "blue")["photo"]
        second = photo["avatars"][1]
        initial = Image.open(io.BytesIO(self.member.get(self.path + "/thumbnail").content))
        self.assertGreater(initial.getpixel((320, 240))[2], 220)
        ids = [second["id"], first["id"]]
        response = self.member.put(self.path + "/layers", json={"version": photo["version"], "avatar_ids": ids})
        self.assertEqual(response.status_code, 200, response.text)
        reordered = response.json()
        self.assertEqual(reordered["version"], photo["version"] + 1)
        self.assertEqual([a["id"] for a in self.member.get(self.path).json()["avatars"]], ids)
        output = Image.open(io.BytesIO(self.member.get(self.path + "/export").content))
        self.assertEqual(output.size, (800, 600))
        self.assertEqual(output.getpixel((400, 300)), (255, 0, 0))
        thumb = Image.open(io.BytesIO(self.member.get(self.path + "/thumbnail").content))
        self.assertGreater(thumb.getpixel((320, 240))[0], 220)
        self.assertLess(thumb.getpixel((320, 240))[2], 30)

        # A pending transform keeps its avatar version and cannot undo the new layers.
        moved = self.member.patch(self.path + "/avatars/" + first["id"],
                                  json={**first, "x": .3, "size": .12, "rotation": 45})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual([a["id"] for a in moved.json()["avatars"]], ids)
        added = self.add("new", "yellow")
        self.assertEqual([a["id"] for a in added["photo"]["avatars"]], ids + [added["selected"]])
        # Layer order must not redefine which avatar establishes the default size.
        self.assertAlmostEqual(added["photo"]["avatars"][-1]["size"], .12)

    def test_layer_validation_and_stale_saves(self):
        self.add("one")
        photo = self.add("two")["photo"]
        ids = [a["id"] for a in photo["avatars"]]
        for invalid in ([], ids[:1], [ids[0], ids[0]], [ids[0], "another-photo-avatar"], ids + ["extra"]):
            response = self.member.put(self.path + "/layers", json={"version": photo["version"], "avatar_ids": invalid})
            self.assertEqual(response.status_code, 400)
        unchanged = self.member.put(self.path + "/layers", json={"version": photo["version"], "avatar_ids": ids}).json()
        self.assertEqual(unchanged["version"], photo["version"])
        self.add("three")
        self.assertEqual(self.member.put(self.path + "/layers", json={"version": photo["version"], "avatar_ids": ids[::-1]}).status_code, 409)
        latest = self.member.get(self.path).json()
        self.assertEqual([a["id"] for a in latest["avatars"][:2]], ids)
        self.assertEqual(len(latest["avatars"]), 3)

    def test_concurrent_layer_saves_do_not_overwrite_each_other(self):
        self.add("one")
        self.add("two")
        photo = self.add("three")["photo"]
        ids = [a["id"] for a in photo["avatars"]]
        orders = [ids[::-1], ids[1:] + ids[:1]]
        def save(order):
            return self.member.put(self.path + "/layers", json={"version": photo["version"], "avatar_ids": order})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, orders))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        winning = next(order for order, response in zip(orders, results) if response.status_code == 200)
        self.assertEqual([a["id"] for a in self.member.get(self.path).json()["avatars"]], winning)


class LayerMigrationTests(unittest.TestCase):
    def test_existing_compositions_survive_upgrade_and_restart(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE avatars (id TEXT PRIMARY KEY, photo_id TEXT, created REAL, username TEXT)")
        original = [("b", "photo1", 1, "B"), ("a", "photo1", 1, "A"), ("c", "photo1", 2, "C"), ("d", "photo2", 1, "D")]
        db.executemany("INSERT INTO avatars VALUES (?,?,?,?)", original)
        migrate_avatar_layers(db)
        ordered = db.execute("SELECT id FROM avatars WHERE photo_id='photo1' ORDER BY z_index").fetchall()
        self.assertEqual([row["id"] for row in ordered], ["a", "b", "c"])
        self.assertEqual(db.execute("SELECT z_index FROM avatars WHERE id='d'").fetchone()[0], 0)
        self.assertEqual([tuple(row) for row in db.execute("SELECT id,photo_id,created,username FROM avatars ORDER BY id")], sorted(original))
        db.execute("UPDATE avatars SET z_index=5 WHERE id='a'")
        migrate_avatar_layers(db)
        self.assertEqual([row["id"] for row in db.execute("SELECT id FROM avatars WHERE photo_id='photo1' ORDER BY z_index")], ["b", "c", "a"])


if __name__ == "__main__":
    unittest.main()
