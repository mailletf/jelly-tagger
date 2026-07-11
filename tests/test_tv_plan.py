import os
from unittest import mock

import pytest

import tv


ARTWORK = {
    "poster": "https://img/poster.jpg",
    "backdrop": "https://img/backdrop.jpg",
    "logo": "https://img/logo.png",
}


def make_client(search_side_effect):
    client = mock.Mock()
    client.search_tv = mock.Mock(side_effect=search_side_effect)
    client.get_tv_images = mock.Mock(return_value=dict(ARTWORK))
    return client


def cand(id, title, year):
    return {"id": id, "title": title, "year": year, "overview": ""}


def make_show(tmp_path, show_folder, files):
    """files: list of (season_folder, filename). Creates and returns paths."""
    paths = []
    for season_folder, filename in files:
        d = tmp_path / show_folder / season_folder
        d.mkdir(parents=True, exist_ok=True)
        f = d / filename
        f.write_text("x")
        paths.append(str(f))
    return paths


def episode_files_for(paths):
    return [(p, *tv.parse_episode(p)) for p in paths]


def test_two_shows_resolved_once_each(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E01.mkv"),
                                             ("Season 01", "Alpha S01E02.mkv")])
    b = make_show(tmp_path, "Beta (2011)", [("Season 01", "Beta S01E01.mkv")])
    # Interleave the file list.
    eps = episode_files_for([a[0], b[0], a[1]])

    def search(name, year):
        return [cand(100, name, year)]

    client = make_client(search)
    dest = tmp_path / "out"
    plan = tv.build_tv_plan(eps, str(dest), client)

    # Exactly one search per show (not per episode).
    assert client.search_tv.call_count == 2
    assert client.get_tv_images.call_count == 2
    # Grouping: 2 alpha episodes + 1 beta episode.
    shows = [p["show"] for p in plan]
    assert shows.count("Alpha") == 2
    assert shows.count("Beta") == 1


def test_dest_paths_match_jellyfin_layout(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E02.mkv")])
    eps = episode_files_for(a)
    client = make_client(lambda name, year: [cand(100, name, year)])
    dest = tmp_path / "out"
    plan = tv.build_tv_plan(eps, str(dest), client)

    p = plan[0]
    expected = os.path.join(
        str(dest), "Alpha (2001) [tmdbid-100]", "Season 01", "Alpha (2001) S01E02.mkv"
    )
    assert p["dest"] == expected
    assert p["show_dir"] == os.path.join(str(dest), "Alpha (2001) [tmdbid-100]")
    assert p["season_dir"] == os.path.join(str(dest), "Alpha (2001) [tmdbid-100]", "Season 01")


def test_artwork_only_on_first_episode(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E01.mkv"),
                                             ("Season 01", "Alpha S01E02.mkv")])
    eps = episode_files_for(a)
    client = make_client(lambda name, year: [cand(100, name, year)])
    dest = tmp_path / "out"
    plan = tv.build_tv_plan(eps, str(dest), client)

    first, second = plan[0], plan[1]
    assert first["artwork_files"] != {}
    assert second["artwork_files"] == {}

    show_dir = first["show_dir"]
    dests = set(first["artwork_files"].values())
    assert os.path.join(show_dir, "poster.jpg") in dests
    assert os.path.join(show_dir, "backdrop.jpg") in dests
    assert os.path.join(show_dir, "logo.png") in dests


def test_sibling_subtitle_with_language_suffix(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E02.mkv")])
    # sibling subtitle with .en language suffix
    sub = tmp_path / "Alpha (2001)" / "Season 01" / "Alpha S01E02.en.srt"
    sub.write_text("x")
    eps = episode_files_for(a)
    client = make_client(lambda name, year: [cand(100, name, year)])
    dest = tmp_path / "out"
    plan = tv.build_tv_plan(eps, str(dest), client)

    subs = plan[0]["subtitles"]
    assert len(subs) == 1
    sub_src, sub_dest = subs[0]
    assert sub_src == str(sub)
    assert os.path.basename(sub_dest) == "Alpha (2001) S01E02.en.srt"


def test_skipping_show_skips_all_its_episodes(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E01.mkv"),
                                             ("Season 01", "Alpha S01E02.mkv")])
    b = make_show(tmp_path, "Beta (2011)", [("Season 01", "Beta S01E01.mkv")])
    eps = episode_files_for([a[0], a[1], b[0]])

    def search(name, year):
        if name == "Alpha":
            # No exact match -> forces a prompt (we will skip).
            return [cand(1, "Something Else", 1999)]
        return [cand(200, name, year)]

    client = make_client(search)
    dest = tmp_path / "out"
    with mock.patch("builtins.input", return_value="s"):
        plan = tv.build_tv_plan(eps, str(dest), client)

    shows = [p["show"] for p in plan]
    assert "Alpha" not in shows
    assert shows == ["Beta"]


def test_auto_match_consumes_no_input(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E01.mkv")])
    eps = episode_files_for(a)
    client = make_client(lambda name, year: [cand(100, name, year)])
    dest = tmp_path / "out"
    with mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        plan = tv.build_tv_plan(eps, str(dest), client)
    assert plan[0]["tmdb_id"] == 100


def test_numbered_pick(tmp_path):
    a = make_show(tmp_path, "Alpha (2001)", [("Season 01", "Alpha S01E01.mkv")])
    eps = episode_files_for(a)

    def search(name, year):
        # No exact match -> numbered list is shown.
        return [cand(11, "First Choice", 2000), cand(22, "Second Choice", 2001)]

    client = make_client(search)
    dest = tmp_path / "out"
    with mock.patch("builtins.input", return_value="2"):
        plan = tv.build_tv_plan(eps, str(dest), client)
    assert plan[0]["tmdb_id"] == 22
    assert plan[0]["show"] == "Second Choice"
