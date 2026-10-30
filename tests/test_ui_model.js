"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const model = require("../web/track-model.js");

const candidate = {genre: "Garage", bpm: null, musical_key: "", purchase_url: "https://music.apple.com/gb/album/example/123?i=456", match: {status: "likely_match"}};
function track(id, extra = {}) {
  return {id: String(id), spotify_id: `track-${id}`, artists: "Example Artist", title_and_version: `Track ${id}`,
    tags: "", bpm: "", musical_key: "", owned: false, local_file: "", _order: id, ...extra};
}

test("pagination exposes every row and clamps after filtering or changing page size", () => {
  const tracks = Array.from({length: 61}, (_, i) => track(i));
  const first = model.paginate(tracks, 1, 25), second = model.paginate(tracks, 2, 25), third = model.paginate(tracks, 3, 25);
  assert.deepEqual([...first.items, ...second.items, ...third.items].map(t => t.id), tracks.map(t => t.id));
  assert.deepEqual([third.start, third.end, third.pages], [51, 61, 3]);
  const shrunk = model.paginate(tracks.slice(0, 3), 3, 25);
  assert.equal(shrunk.page, 1);
  assert.equal(model.paginate(tracks, 0, 999).pageSize, 25);
  assert.deepEqual([model.paginate([], 10, 25).page, model.paginate([], 10, 25).start], [1, 0]);
});

test("BPM sorts numerically with unknown values last and preserves the source collection", () => {
  const tracks = [track(1, {bpm: "174"}), track(2, {bpm: "9"}), track(3), track(4, {bpm: "128.5"}), track(5, {bpm: "not known"})];
  const result = model.select(tracks, {sort: "bpm"});
  assert.deepEqual(result.map(t => t.id), ["2", "4", "1", "3", "5"]);
  assert.deepEqual(tracks.map(t => t.id), ["1", "2", "3", "4", "5"]);
});

test("suggested metadata remains separate and never replaces saved fields", () => {
  const saved = track(1, {tags: "warm-up; personal", bpm: "130", musical_key: "C minor",
    store_lookup: {status: "matched", offer: {...candidate, bpm: 131, musical_key: "D minor"}}});
  assert.deepEqual(model.tags(saved), {own: ["warm-up", "personal"], genre: "Garage"});
  assert.equal(model.metadata(saved).bpm, "130");
  assert.equal(model.metadata(saved).key, "C minor");
  assert.equal(saved.tags, "warm-up; personal");
  assert.equal(saved.store_lookup.offer.bpm, 131);
  assert.equal(model.tags(track(2, {tags: "garage", store_lookup: saved.store_lookup})).genre, "");
});

test("ambiguous and failed lookups do not supply automatic metadata", () => {
  for (const status of ["review", "error", "not_found", "queued", "searching", "interrupted"]) {
    const result = track(1, {store_lookup: {status, offer: {...candidate, bpm: 170, musical_key: "A minor"}}});
    assert.equal(model.storeOffer(result), null);
    assert.equal(model.tags(result).genre, "");
    assert.equal(model.metadata(result).bpm, "");
  }
});

test("combined filters find unowned library copies and search sourced genre tags", () => {
  const matches = track(1, {_candidate_count: 2, store_lookup: {status: "matched", offer: candidate}});
  const owned = track(2, {owned: true, _candidate_count: 1, tags: "Garage"});
  const linked = track(3, {local_file: "/example/file.flac", file_available: false, _candidate_count: 1});
  const tracks = [matches, owned, linked];
  assert.deepEqual(model.select(tracks, {filter: "wanted", libraryFilter: "review", query: "garage"}).map(t => t.id), ["1"]);
  assert.deepEqual(model.select(tracks, {libraryFilter: "missing"}).map(t => t.id), ["3"]);
  assert.deepEqual(model.select(tracks, {storeFilter: "no_link"}).map(t => t.id), ["3", "2"]);
  assert.deepEqual(model.select(tracks, {storeFilter: "matched"}).map(t => t.id), ["1"]);
});

test("artwork URLs use only the configured HTTPS providers and colour stays stable", () => {
  assert.equal(model.artworkURL("https://i.scdn.co/image/abc"), "https://i.scdn.co/image/abc");
  assert.equal(model.artworkURL("https://is1-ssl.mzstatic.com/image/thumb/example"), "https://is1-ssl.mzstatic.com/image/thumb/example");
  for (const value of ["http://i.scdn.co/image/abc", "https://i.scdn.co.attacker.example/a", "https://user:pass@i.scdn.co/a", "https://i.scdn.co:9000/a", "https://is6-ssl.mzstatic.com/a", "data:image/svg+xml,example"]) {
    assert.equal(model.artworkURL(value), "");
  }
  assert.equal(model.tone(track(1)), model.tone(track(1, {artists: "Changed credit"})));
  assert.ok(new Set(Array.from({length: 10}, (_, i) => model.tone(track(i)))).size > 1);
});
