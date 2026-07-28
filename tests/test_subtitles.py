import os
from unittest import mock

import pytest

import subtitles
from subtitles import (
    CHUNK_SIZE,
    OpenSubtitlesClient,
    best_per_language,
    compute_osdb_hash,
)


# ---------------------------------------------------------------------------
# compute_osdb_hash
#
# For an all-zero file every 8-byte word contributes 0, so the hash is exactly
# the file size. That gives an independent expected value to test against
# without reimplementing the algorithm in the test.
# ---------------------------------------------------------------------------

def write_video(path, size, patches=()):
    """Write a file of `size` zero bytes, then apply (offset, bytes) patches."""
    with open(path, "wb") as f:
        f.write(b"\0" * size)
        for offset, payload in patches:
            f.seek(offset)
            f.write(payload)
    return str(path)


def test_osdb_hash_of_zero_file_is_its_size(tmp_path):
    size = 200 * 1024
    path = write_video(tmp_path / "v.mkv", size)
    assert compute_osdb_hash(path) == (f"{size:016x}", size)


def test_osdb_hash_counts_both_head_and_tail(tmp_path):
    size = 200 * 1024
    # +1 from the first word of the head, +2 from the last word of the tail.
    path = write_video(tmp_path / "v.mkv", size, [
        (0, b"\x01" + b"\0" * 7),
        (size - 8, b"\x02" + b"\0" * 7),
    ])
    assert compute_osdb_hash(path) == (f"{size + 3:016x}", size)


def test_osdb_hash_ignores_the_middle_of_the_file(tmp_path):
    size = 400 * 1024
    plain = write_video(tmp_path / "a.mkv", size)
    # Well past the first 64KB and well before the last 64KB.
    patched = write_video(tmp_path / "b.mkv", size, [(200 * 1024, b"\xff" * 8)])
    assert compute_osdb_hash(plain) == compute_osdb_hash(patched)


def test_osdb_hash_none_for_file_under_128kb(tmp_path):
    size = CHUNK_SIZE * 2 - 1
    path = write_video(tmp_path / "small.mkv", size)
    assert compute_osdb_hash(path) == (None, size)


# ---------------------------------------------------------------------------
# best_per_language
# ---------------------------------------------------------------------------

def sub_result(language, file_id, download_count=0, moviehash_match=False, release=""):
    return {"attributes": {
        "language": language,
        "download_count": download_count,
        "moviehash_match": moviehash_match,
        "release": release,
        "files": [{"file_id": file_id}],
    }}


def test_best_per_language_prefers_exact_release_match():
    results = [
        sub_result("en", 1, download_count=9999),
        sub_result("en", 2, download_count=5, moviehash_match=True),
    ]
    picked = best_per_language(results, ("en",))
    assert [p["file_id"] for p in picked] == [2]


def test_best_per_language_falls_back_to_download_count():
    results = [sub_result("en", 1, download_count=5), sub_result("en", 2, download_count=50)]
    assert [p["file_id"] for p in best_per_language(results, ("en",))] == [2]


def test_best_per_language_returns_one_per_language_in_requested_order():
    results = [sub_result("fr", 1), sub_result("en", 2), sub_result("en", 3, download_count=1)]
    picked = best_per_language(results, ("en", "fr"))
    assert [(p["language"], p["file_id"]) for p in picked] == [("en", 3), ("fr", 1)]


def test_best_per_language_skips_languages_not_requested():
    assert best_per_language([sub_result("de", 1)], ("en",)) == []


def test_best_per_language_skips_entries_without_a_file_id():
    results = [{"attributes": {"language": "en", "files": []}}, sub_result("en", 7)]
    assert [p["file_id"] for p in best_per_language(results, ("en",))] == [7]


# ---------------------------------------------------------------------------
# OpenSubtitlesClient
# ---------------------------------------------------------------------------

def test_find_for_file_queries_by_hash(tmp_path):
    path = write_video(tmp_path / "v.mkv", 200 * 1024)
    client = OpenSubtitlesClient("key", langs=("en", "fr"))
    calls = []

    def fake_request(p, params=None, payload=None):
        calls.append((p, params, payload))
        return {"data": [sub_result("en", 42, moviehash_match=True)]}

    with mock.patch.object(client, "_request", side_effect=fake_request):
        found = client.find_for_file(path)

    assert calls[0][0] == "/subtitles"
    assert calls[0][1] == {"moviehash": f"{200 * 1024:016x}", "languages": "en,fr"}
    assert [f["file_id"] for f in found] == [42]


def test_find_for_file_skips_api_call_when_file_too_small(tmp_path):
    path = write_video(tmp_path / "tiny.mkv", 1024)
    client = OpenSubtitlesClient("key")
    with mock.patch.object(client, "_request", side_effect=AssertionError("should not call API")):
        assert client.find_for_file(path) == []


def test_download_link_logs_in_once_when_credentials_given():
    client = OpenSubtitlesClient("key", username="u", password="p")
    calls = []

    def fake_request(path, params=None, payload=None):
        calls.append(path)
        if path == "/login":
            return {"token": "tok"}
        return {"link": "https://dl.example/sub.srt"}

    with mock.patch.object(client, "_request", side_effect=fake_request):
        assert client.download_link(1) == "https://dl.example/sub.srt"
        assert client.download_link(2) == "https://dl.example/sub.srt"

    assert calls.count("/login") == 1
    assert client._token == "tok"


def test_download_link_skips_login_without_credentials():
    client = OpenSubtitlesClient("key")
    with mock.patch.object(client, "_request", return_value={"link": "u"}) as req:
        assert client.download_link(1) == "u"
    assert [c.args[0] for c in req.call_args_list] == ["/download"]


def test_failed_login_is_not_retried_for_every_file():
    """Retrying bad credentials once per file is how accounts get banned."""
    client = OpenSubtitlesClient("key", username="u", password="wrong")
    calls = []

    def fake_request(path, params=None, payload=None):
        calls.append(path)
        raise RuntimeError("OpenSubtitles API error (401)")

    with mock.patch.object(client, "_request", side_effect=fake_request):
        for _ in range(5):
            with pytest.raises(RuntimeError):
                client.download_link(1)

    assert calls.count("/login") == 1


def test_download_link_raises_when_no_link_returned():
    client = OpenSubtitlesClient("key")
    with mock.patch.object(client, "_request", return_value={}):
        with pytest.raises(RuntimeError, match="no download link"):
            client.download_link(5)
