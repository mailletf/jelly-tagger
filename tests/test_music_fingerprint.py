import os
from unittest import mock

import jelly_tagger
from jelly_tagger import UNKNOWN_ALBUM, UNKNOWN_ARTIST, build_plan, fill_from_fingerprint


class FakeFingerprinter:
    """Stands in for AcoustIDClient, recording which files it was asked about."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.looked_up = []

    def lookup(self, path):
        self.looked_up.append(path)
        if self.error:
            raise self.error
        return self.result


MATCH = {"artist": "Radiohead", "album": "OK Computer", "title": "Karma Police"}


# ---------------------------------------------------------------------------
# fill_from_fingerprint
# ---------------------------------------------------------------------------

def test_fills_in_every_missing_field():
    fp = FakeFingerprinter(MATCH)
    result = fill_from_fingerprint("/m/track01.mp3", "", "", "", fp)
    assert result == ("Radiohead", "OK Computer", "Karma Police")


def test_existing_tags_win_over_the_fingerprint_match():
    fp = FakeFingerprinter(MATCH)
    # Only the album is missing, so only the album may be replaced.
    result = fill_from_fingerprint("/m/track01.mp3", "My Artist", "", "My Title", fp)
    assert result == ("My Artist", "OK Computer", "My Title")


def test_complete_tags_are_never_sent_to_acoustid():
    fp = FakeFingerprinter(MATCH)
    result = fill_from_fingerprint("/m/track01.mp3", "Artist", "Album", "Title", fp)
    assert result == ("Artist", "Album", "Title")
    assert fp.looked_up == []


def test_title_tag_matching_the_filename_is_still_a_real_tag():
    # "Karma Police.mp3" tagged title="Karma Police" is fully tagged; it must
    # not be fingerprinted, and must not be renamed to a remaster's title.
    fp = FakeFingerprinter({
        "artist": "Radiohead", "album": "OK Computer",
        "title": "Karma Police (Remastered 2017)",
    })
    result = fill_from_fingerprint(
        "/m/Karma Police.mp3", "Radiohead", "OK Computer", "Karma Police", fp
    )
    assert result == ("Radiohead", "OK Computer", "Karma Police")
    assert fp.looked_up == []


def test_band_actually_named_unknown_artist_is_not_overwritten():
    # A real artist tag reading "Unknown Artist" is a present tag, not a
    # placeholder: only the genuinely absent title may be filled in.
    fp = FakeFingerprinter(MATCH)
    result = fill_from_fingerprint("/m/01.mp3", UNKNOWN_ARTIST, "Real Album", "", fp)
    assert result == (UNKNOWN_ARTIST, "Real Album", "Karma Police")


def test_lookup_failure_leaves_tags_untouched(capsys):
    fp = FakeFingerprinter(error=RuntimeError("fpcalc exploded"))
    result = fill_from_fingerprint("/m/track01.mp3", "", "", "", fp)
    assert result == ("", "", "")
    assert "fpcalc exploded" in capsys.readouterr().out


def test_non_runtime_lookup_errors_are_also_survivable(capsys):
    # A non-executable --fpcalc raises PermissionError, a captive portal makes
    # json.loads raise ValueError. Neither may abort the whole run.
    for boom in (PermissionError("not executable"), ValueError("not json")):
        fp = FakeFingerprinter(error=boom)
        assert fill_from_fingerprint("/m/track01.mp3", "", "", "", fp) == ("", "", "")
    assert "could not fingerprint" in capsys.readouterr().out


def test_no_match_leaves_tags_untouched(capsys):
    fp = FakeFingerprinter(None)
    result = fill_from_fingerprint("/m/track01.mp3", "", "", "", fp)
    assert result == ("", "", "")
    assert "No AcoustID match" in capsys.readouterr().out


def test_partial_match_only_fills_what_it_found():
    fp = FakeFingerprinter({"artist": "Radiohead", "album": "", "title": "Karma Police"})
    result = fill_from_fingerprint("/m/track01.mp3", "", "", "", fp)
    assert result == ("Radiohead", "", "Karma Police")


# ---------------------------------------------------------------------------
# build_plan integration
# ---------------------------------------------------------------------------

def test_build_plan_uses_fingerprint_for_untagged_file(tmp_path):
    # A file with no readable ID3 tags: read_tags falls back to placeholders.
    track = tmp_path / "track01.mp3"
    track.write_text("not really audio")
    fp = FakeFingerprinter(MATCH)

    plan = build_plan([str(track)], str(tmp_path / "out"), fingerprinter=fp)

    assert fp.looked_up == [str(track)]
    assert plan[0]["dest"] == os.path.join(
        str(tmp_path / "out"), "Radiohead", "OK Computer", "Karma Police.mp3"
    )


def test_build_plan_without_fingerprinter_keeps_unknown_folders(tmp_path):
    track = tmp_path / "track01.mp3"
    track.write_text("not really audio")

    plan = build_plan([str(track)], str(tmp_path / "out"))

    assert plan[0]["dest"] == os.path.join(
        str(tmp_path / "out"), UNKNOWN_ARTIST, UNKNOWN_ALBUM, "track01.mp3"
    )
