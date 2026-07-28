import json
import subprocess
from unittest import mock

import pytest

import fingerprint
from fingerprint import AcoustIDClient, _pick_album, best_recording, fpcalc


# ---------------------------------------------------------------------------
# fpcalc
# ---------------------------------------------------------------------------

def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_fpcalc_parses_json_output():
    out = json.dumps({"duration": 213.4, "fingerprint": "AQAB"})
    with mock.patch.object(subprocess, "run", return_value=completed(stdout=out)):
        assert fpcalc("/song.mp3") == (213, "AQAB")


def test_fpcalc_missing_binary_explains_how_to_install():
    with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="chromaprint"):
            fpcalc("/song.mp3")


def test_fpcalc_nonzero_exit_raises():
    with mock.patch.object(subprocess, "run", return_value=completed(1, stderr="bad file")):
        with pytest.raises(RuntimeError, match="bad file"):
            fpcalc("/song.mp3")


def test_fpcalc_unparseable_output_raises():
    with mock.patch.object(subprocess, "run", return_value=completed(stdout="not json")):
        with pytest.raises(RuntimeError, match="Could not parse"):
            fpcalc("/song.mp3")


# ---------------------------------------------------------------------------
# _pick_album
# ---------------------------------------------------------------------------

def test_pick_album_prefers_a_studio_album_over_a_compilation():
    groups = [
        {"title": "Greatest Hits", "type": "Album", "secondarytypes": ["Compilation"]},
        {"title": "Real Album", "type": "Album"},
    ]
    assert _pick_album(groups) == "Real Album"


def test_pick_album_falls_back_to_first_when_no_studio_album():
    groups = [{"title": "Some Soundtrack", "type": "Soundtrack"}]
    assert _pick_album(groups) == "Some Soundtrack"


def test_pick_album_empty():
    assert _pick_album([]) == ""


# ---------------------------------------------------------------------------
# best_recording
# ---------------------------------------------------------------------------

def recording(title, artist="An Artist", album="An Album"):
    return {
        "title": title,
        "artists": [{"name": artist}],
        "releasegroups": [{"title": album, "type": "Album"}],
    }


def test_best_recording_takes_the_highest_scoring_result():
    results = [
        {"score": 0.4, "recordings": [recording("Low Score")]},
        {"score": 0.98, "recordings": [recording("High Score", "Real Artist", "Real Album")]},
    ]
    assert best_recording(results) == {
        "artist": "Real Artist", "album": "Real Album", "title": "High Score",
    }


def test_best_recording_skips_results_without_a_usable_recording():
    results = [
        {"score": 0.99, "recordings": []},
        {"score": 0.9, "recordings": [{"title": ""}]},
        {"score": 0.5, "recordings": [recording("Found It")]},
    ]
    assert best_recording(results)["title"] == "Found It"


def test_best_recording_none_when_nothing_identifiable():
    assert best_recording([]) is None
    assert best_recording([{"score": 1.0, "recordings": []}]) is None


def test_best_recording_tolerates_missing_artist_and_album():
    results = [{"score": 1.0, "recordings": [{"title": "Bare"}]}]
    assert best_recording(results) == {"artist": "", "album": "", "title": "Bare"}


# ---------------------------------------------------------------------------
# AcoustIDClient.lookup
# ---------------------------------------------------------------------------

def test_lookup_posts_fingerprint_and_returns_match():
    client = AcoustIDClient("apikey")
    posted = {}

    def fake_post(params):
        posted.update(params)
        return {"status": "ok", "results": [{"score": 1.0, "recordings": [recording("Song")]}]}

    with mock.patch.object(fingerprint, "fpcalc", return_value=(200, "FPRINT")), \
         mock.patch.object(client, "_post", side_effect=fake_post):
        result = client.lookup("/song.mp3")

    assert posted["client"] == "apikey"
    assert posted["fingerprint"] == "FPRINT"
    assert posted["duration"] == 200
    assert result["title"] == "Song"


def test_lookup_raises_on_api_error_status():
    client = AcoustIDClient("apikey")
    error = {"status": "error", "error": {"message": "invalid api key"}}
    with mock.patch.object(fingerprint, "fpcalc", return_value=(200, "FP")), \
         mock.patch.object(client, "_post", return_value=error):
        with pytest.raises(RuntimeError, match="invalid api key"):
            client.lookup("/song.mp3")


def test_lookup_returns_none_when_unidentified():
    client = AcoustIDClient("apikey")
    with mock.patch.object(fingerprint, "fpcalc", return_value=(200, "FP")), \
         mock.patch.object(client, "_post", return_value={"status": "ok", "results": []}):
        assert client.lookup("/song.mp3") is None
