"""Metadata proposals require review and only affect DJList-owned records."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from library_inventory import LibraryInventory, _identity
from metadata_review import MetadataReview, PLAN_SECONDS, suggest_metadata
from spotify_api import AppError


class MetadataReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.store = Store(self.root / "data")
        self.store.put_settings({"mode": "real", "music_root": str(self.music)})
        self.library = LibraryInventory(self.store)
        self.clock = [0]
        self.review = MetadataReview(self.store, self.library, clock=lambda: self.clock[0])
        self.path = self.music / "Odd Name.flac"
        self.path.write_bytes(b"unchanged audio bytes")
        self.import_xml()
        self.identity = _identity(str(self.path))
        self.store.upsert([{"spotify_id": "abc", "artists": "Spotify artist", "title_and_version": "Spotify title"}], "fixture")
        self.track_id = self.store.rows("real")[0]["id"]
        self.store.patch(self.track_id, "real", {"owned": False, "notes": "Keep notes", "tags": "keep", "cue_notes": "keep cues"})

    def tearDown(self):
        self.temporary.cleanup()

    def import_xml(self, artist="", title="Sub Focus - Massive Track (ft. AnotherArtist) (Extended Mix)"):
        root = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(root, "COLLECTION", Entries="1")
        ET.SubElement(collection, "TRACK", Name=title, Artist=artist, Location=self.path.as_uri())
        self.library.import_xml(ET.tostring(root, encoding="unicode"), str(self.music))

    def entry(self):
        self.library.invalidate()
        return self.library.review([], str(self.music))["entries"][0]

    def test_parser_separates_credit_and_preserves_remix(self):
        value = suggest_metadata({"artist": "", "title": "Sub Focus - Massive Track (ft. AnotherArtist) (Extended Mix)"})
        self.assertEqual(value["after"], {"artist": "Sub Focus", "title": "Massive Track (Extended Mix)",
                                          "featured_artists": "AnotherArtist", "version_notes": "Extended Mix"})
        self.assertEqual(value["confidence"], "review")
        self.assertTrue(value["reasons"])

    def test_parser_does_not_split_hyphenated_artist_or_numeric_titles(self):
        for title in ("99 Problems", "AC-DC", "Jay-Z", "05 Something", "Title feat imagination"):
            self.assertIsNone(suggest_metadata({"artist": "", "title": title}))
        value = suggest_metadata({"artist": "AC-DC", "title": "AC-DC - Track"})
        self.assertEqual(value["after"]["artist"], "AC-DC")
        self.assertEqual(value["after"]["title"], "Track")

    def test_different_existing_artist_is_not_overwritten(self):
        self.assertIsNone(suggest_metadata({"artist": "Confirmed Artist", "title": "Another Artist - Song"}))

    def test_apply_preserves_audio_spotify_and_annotations_and_survives_refresh(self):
        before = self.store.rows("real")
        source = self.path.read_bytes()
        proposal = self.review.preview([self.identity])
        self.assertEqual(proposal["summary"]["proposed"], 1)
        self.assertFalse(self.entry()["metadata_override"])
        result = self.review.apply(proposal["token"], [self.identity])
        self.assertEqual(result["count"], 1)
        entry = self.entry()
        self.assertEqual((entry["artist"], entry["title"], entry["featured_artists"]),
                         ("Sub Focus", "Massive Track (Extended Mix)", "AnotherArtist"))
        self.assertTrue(entry["metadata_override"])
        self.assertIsNone(entry["metadata_review_hint"])
        self.import_xml()
        self.assertTrue(self.entry()["metadata_override"])
        self.assertEqual(self.store.rows("real"), before)
        self.assertEqual(self.path.read_bytes(), source)
        with self.assertRaises(AppError):
            self.review.apply(proposal["token"], [self.identity])

    def test_reset_is_reviewed_and_restores_original_source_fields(self):
        proposal = self.review.preview([self.identity])
        self.review.apply(proposal["token"], [self.identity])
        reset = self.review.reset_preview([self.identity])
        self.assertEqual(reset["items"][0]["after"]["artist"], "")
        self.review.reset(reset["token"], [self.identity])
        self.assertFalse(self.entry()["metadata_override"])
        self.assertEqual(self.entry()["artist"], "")

    def test_changed_file_invalidates_override_and_plan(self):
        proposal = self.review.preview([self.identity])
        self.path.write_bytes(b"replacement audio")
        with self.assertRaises(AppError):
            self.review.apply(proposal["token"], [self.identity])
        proposal = self.review.preview([self.identity])
        self.review.apply(proposal["token"], [self.identity])
        self.path.write_bytes(b"second replacement")
        entry = self.entry()
        self.assertFalse(entry["metadata_override"])
        self.assertTrue(entry["metadata_override_stale"])

    def test_metadata_refresh_settings_change_and_expiry_reject_stale_plans(self):
        proposal = self.review.preview([self.identity])
        self.import_xml(title="Different Artist - Another Song")
        with self.assertRaises(AppError):
            self.review.apply(proposal["token"], [self.identity])
        proposal = self.review.preview([self.identity])
        self.clock[0] += PLAN_SECONDS + 1
        with self.assertRaises(AppError):
            self.review.apply(proposal["token"], [self.identity])
        proposal = self.review.preview([self.identity])
        self.store.put_settings({"download_root": str(self.root / "other")})
        with self.assertRaises(AppError):
            self.review.apply(proposal["token"], [self.identity])

    def test_empty_or_unreviewed_selection_and_demo_rejected(self):
        proposal = self.review.preview([self.identity])
        for ids in ([], ["0" * 24], [self.identity, self.identity]):
            with self.subTest(ids=ids), self.assertRaises(AppError):
                self.review.apply(proposal["token"], ids)
        for ids in ([], [{}], [self.identity] * 101, ["x"]):
            with self.subTest(ids=ids), self.assertRaises(AppError):
                self.review.preview(ids)
        self.store.put_settings({"mode": "demo"})
        with self.assertRaises(AppError):
            self.review.preview([self.identity])

    def test_metadata_filter_and_duplicate_member_evidence(self):
        entries = self.library.review([], str(self.music), filter_name="metadata")
        self.assertEqual(entries["total"], 1)
        self.assertEqual(entries["summary"]["metadata_review_count"], 1)


if __name__ == "__main__":
    unittest.main()
