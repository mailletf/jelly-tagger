import os
from unittest import mock

import pytest

import tv


# ---------------------------------------------------------------------------
# parse_episode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("Show.Name.S01E02.720p.x264.mkv", (1, 2)),
    ("Show Name - 1x02 - Episode Title.mkv", (1, 2)),
    ("show name s1e2.mkv", (1, 2)),
    ("Show.Name.S01E02E03.mkv", (1, 2)),          # multi-episode -> first ep
    ("Show.Name.S01E02-E03.mkv", (1, 2)),         # multi-episode -> first ep
])
def test_parse_episode_from_filename(filename, expected):
    assert tv.parse_episode(filename) == expected


def test_parse_episode_season_folder_named_number(tmp_path):
    d = tmp_path / "Season 1"
    d.mkdir()
    f = d / "Show Name 02.mkv"
    f.write_text("x")
    assert tv.parse_episode(str(f)) == (1, 2)


def test_parse_episode_season_folder_zero_padded(tmp_path):
    d = tmp_path / "Season 01"
    d.mkdir()
    f = d / "02 - Pilot.mkv"
    f.write_text("x")
    assert tv.parse_episode(str(f)) == (1, 2)


@pytest.mark.parametrize("filename", [
    "Some Random Movie.mkv",
    "vacation footage.mkv",
])
def test_parse_episode_unparseable(filename):
    assert tv.parse_episode(filename) is None


# ---------------------------------------------------------------------------
# guess_show_name
# ---------------------------------------------------------------------------

def test_guess_show_name_strips_episode_token_and_title(tmp_path):
    # File sits directly in scan root -> falls back to filename parsing.
    f = tmp_path / "Show Name - 1x02 - Episode Title.mkv"
    f.write_text("x")
    name, year = tv.guess_show_name(str(f), str(tmp_path))
    assert name == "Show Name"
    assert year is None


def test_guess_show_name_strips_junk_tags(tmp_path):
    f = tmp_path / "Show.Name.S01E02.720p.x264.mkv"
    f.write_text("x")
    name, year = tv.guess_show_name(str(f), str(tmp_path))
    assert name == "Show Name"
    assert year is None


def test_guess_show_name_year_in_filename(tmp_path):
    f = tmp_path / "Show.Name.2010.S01E02.720p.mkv"
    f.write_text("x")
    name, year = tv.guess_show_name(str(f), str(tmp_path))
    assert name == "Show Name"
    assert year == 2010


def test_guess_show_name_year_in_folder(tmp_path):
    show_dir = tmp_path / "Show Name (2010)"
    show_dir.mkdir()
    f = show_dir / "Show.Name.S01E02.mkv"
    f.write_text("x")
    # scan_root is the grandparent so the show folder is preferred.
    name, year = tv.guess_show_name(str(f), str(tmp_path))
    assert name == "Show Name"
    assert year == 2010


def test_guess_show_name_parent_folder_preference(tmp_path):
    # file inside "Breaking Bad (2008)/Season 01/" gets name from show folder,
    # not the (deliberately misleading) filename.
    show_dir = tmp_path / "Breaking Bad (2008)"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    f = season_dir / "totally.different.name.S01E05.mkv"
    f.write_text("x")
    name, year = tv.guess_show_name(str(f), str(tmp_path))
    assert name == "Breaking Bad"
    assert year == 2008


def test_guess_show_name_file_in_scan_root_uses_filename(tmp_path):
    f = tmp_path / "The Wire S01E01.mkv"
    f.write_text("x")
    name, year = tv.guess_show_name(str(f), str(tmp_path))
    assert name == "The Wire"
    assert year is None


# ---------------------------------------------------------------------------
# find_episode_files
# ---------------------------------------------------------------------------

def test_find_episode_files_only_video_extensions(tmp_path):
    (tmp_path / "Show.S01E01.mkv").write_text("x")
    (tmp_path / "Show.S01E02.mp4").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "Show.S01E01.en.srt").write_text("x")  # subtitle, not a video

    with mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        result = tv.find_episode_files(str(tmp_path))

    got = sorted((os.path.basename(p), s, e) for p, s, e in result)
    assert got == [
        ("Show.S01E01.mkv", 1, 1),
        ("Show.S01E02.mp4", 1, 2),
    ]


def test_find_episode_files_prompt_supplies_episode(tmp_path):
    (tmp_path / "Show.S01E01.mkv").write_text("x")
    (tmp_path / "mystery clip.mkv").write_text("x")  # unparseable

    with mock.patch("builtins.input", return_value="S01E05"):
        result = tv.find_episode_files(str(tmp_path))

    by_name = {os.path.basename(p): (s, e) for p, s, e in result}
    assert by_name["Show.S01E01.mkv"] == (1, 1)
    assert by_name["mystery clip.mkv"] == (1, 5)


def test_find_episode_files_prompt_skip(tmp_path):
    (tmp_path / "Show.S01E01.mkv").write_text("x")
    (tmp_path / "mystery clip.mkv").write_text("x")

    with mock.patch("builtins.input", return_value="s"):
        result = tv.find_episode_files(str(tmp_path))

    names = {os.path.basename(p) for p, _, _ in result}
    assert names == {"Show.S01E01.mkv"}
