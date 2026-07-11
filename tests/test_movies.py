import os
from unittest import mock

import pytest

import movies
from movies import (
    TMDB_IMAGE_BASE,
    TMDBClient,
    _best_image,
    build_movie_plan,
    execute_movie_plan,
    guess_title_year,
    resolve_interactive,
)


# ---------------------------------------------------------------------------
# guess_title_year
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("Juno.2007.1080p.BluRay.x264-GROUP.mkv", ("Juno", 2007)),
    ("The.Matrix.1999.REMUX.mkv", ("The Matrix", 1999)),
    ("Interstellar (2014).mkv", ("Interstellar", 2014)),
    ("Some.Movie.1080p.WEBRip.mkv", ("Some Movie", None)),
    ("Some Weird Movie Name.mkv", ("Some Weird Movie Name", None)),
])
def test_guess_title_year(filename, expected):
    assert guess_title_year(filename) == expected


# ---------------------------------------------------------------------------
# _best_image
# ---------------------------------------------------------------------------

def test_best_image_empty():
    assert _best_image([]) is None
    assert _best_image(None) is None


def test_best_image_prefer_lang():
    images = [
        {"file_path": "/fr.jpg", "iso_639_1": "fr", "vote_count": 100},
        {"file_path": "/en.jpg", "iso_639_1": "en", "vote_count": 5},
    ]
    assert _best_image(images, prefer_lang="en") == TMDB_IMAGE_BASE + "/en.jpg"


def test_best_image_prefer_lang_falls_back_when_no_match():
    images = [
        {"file_path": "/fr.jpg", "iso_639_1": "fr", "vote_count": 100},
    ]
    # No "en" images: filter is a no-op, best overall is returned.
    assert _best_image(images, prefer_lang="en") == TMDB_IMAGE_BASE + "/fr.jpg"


def test_best_image_vote_count_ordering():
    images = [
        {"file_path": "/low.jpg", "vote_count": 1},
        {"file_path": "/high.jpg", "vote_count": 50},
        {"file_path": "/none.jpg"},  # missing vote_count -> treated as 0
    ]
    assert _best_image(images) == TMDB_IMAGE_BASE + "/high.jpg"


def test_best_image_url_prefixing():
    assert _best_image([{"file_path": "/x.jpg"}]) == "https://image.tmdb.org/t/p/original/x.jpg"


# ---------------------------------------------------------------------------
# search_movie / search_tv result mapping
# ---------------------------------------------------------------------------

def test_search_movie_maps_raw_json():
    client = TMDBClient("key")
    raw = {"results": [
        {"id": 1, "title": "Juno", "release_date": "2007-12-05", "overview": "A teen."},
        {"id": 2, "original_title": "Sans Titre", "overview": ""},  # no release_date, no title
    ]}
    with mock.patch.object(client, "_get", return_value=raw) as get:
        out = client.search_movie("Juno", year=2007)

    get.assert_called_once_with("/search/movie", {"query": "Juno", "include_adult": "false", "year": "2007"})
    assert out == [
        {"id": 1, "title": "Juno", "year": 2007, "overview": "A teen."},
        {"id": 2, "title": "Sans Titre", "year": None, "overview": ""},
    ]


def test_search_tv_maps_raw_json():
    client = TMDBClient("key")
    raw = {"results": [
        {"id": 10, "name": "Breaking Bad", "first_air_date": "2008-01-20", "overview": "Chem."},
        {"id": 11, "original_name": "Untitled", "first_air_date": "", "overview": ""},
    ]}
    with mock.patch.object(client, "_get", return_value=raw) as get:
        out = client.search_tv("Breaking Bad", year=2008)

    get.assert_called_once_with(
        "/search/tv", {"query": "Breaking Bad", "include_adult": "false", "first_air_date_year": "2008"}
    )
    assert out == [
        {"id": 10, "title": "Breaking Bad", "year": 2008, "overview": "Chem."},
        {"id": 11, "title": "Untitled", "year": None, "overview": ""},
    ]


# ---------------------------------------------------------------------------
# resolve_interactive
# ---------------------------------------------------------------------------

def cand(id, title, year, overview=""):
    return {"id": id, "title": title, "year": year, "overview": overview}


def test_resolve_interactive_auto_match():
    search_fn = mock.Mock(return_value=[cand(1, "Juno", 2007)])
    with mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        out = resolve_interactive("hdr", search_fn, "Juno", 2007, "prompt: ")
    assert out["id"] == 1


def test_resolve_interactive_numbered_pick():
    search_fn = mock.Mock(return_value=[cand(1, "A", 2000), cand(2, "B", 2001)])
    with mock.patch("builtins.input", return_value="2"):
        out = resolve_interactive("hdr", search_fn, "Something", None, "prompt: ")
    assert out["id"] == 2


def test_resolve_interactive_free_text_research_then_match():
    def search(name, year):
        if name == "Wrong Name":
            return []
        return [cand(9, "Right Name", 2010)]

    search_fn = mock.Mock(side_effect=search)
    with mock.patch("builtins.input", return_value="Right Name"):
        out = resolve_interactive("hdr", search_fn, "Wrong Name", 1999, "prompt: ")
    assert out["id"] == 9
    # First search with guess, second with the typed term (year reset).
    assert search_fn.call_args_list == [
        mock.call("Wrong Name", 1999),
        mock.call("Right Name", None),
    ]


def test_resolve_interactive_skip():
    search_fn = mock.Mock(return_value=[])
    with mock.patch("builtins.input", return_value="s"):
        assert resolve_interactive("hdr", search_fn, "Nothing", None, "prompt: ") is None


# ---------------------------------------------------------------------------
# build_movie_plan + execute_movie_plan happy path
# ---------------------------------------------------------------------------

def fake_get(path, params):
    if path == "/search/movie":
        return {"results": [
            {"id": 123, "title": "Juno", "release_date": "2007-12-05", "overview": ""},
        ]}
    if path == "/movie/123/images":
        return {
            "posters": [{"file_path": "/p.jpg", "vote_count": 3}],
            "backdrops": [{"file_path": "/b.jpg", "vote_count": 2}],
            "logos": [{"file_path": "/l.png", "iso_639_1": "en", "vote_count": 1}],
        }
    raise AssertionError(f"unexpected path {path}")


def fake_download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(b"art")


def test_build_and_execute_movie_plan(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    video = src_dir / "Juno.2007.1080p.BluRay.x264-GROUP.mkv"
    video.write_text("movie")
    sub = src_dir / "Juno.2007.1080p.BluRay.x264-GROUP.en.srt"
    sub.write_text("subs")

    dest = tmp_path / "out"
    client = TMDBClient("key")

    with mock.patch.object(client, "_get", side_effect=fake_get), \
         mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        plan = build_movie_plan([str(video)], str(dest), client)

    assert len(plan) == 1
    p = plan[0]
    movie_dir = os.path.join(str(dest), "Juno (2007) [tmdbid-123]")
    assert p["movie_dir"] == movie_dir
    assert p["dest"] == os.path.join(movie_dir, "Juno (2007).mkv")
    assert p["subtitles"] == [(str(sub), os.path.join(movie_dir, "Juno (2007).en.srt"))]
    assert set(p["artwork_files"].values()) == {
        os.path.join(movie_dir, "Juno (2007).jpg"),
        os.path.join(movie_dir, "backdrop.jpg"),
        os.path.join(movie_dir, "logo.png"),
    }

    with mock.patch.object(movies, "_download", side_effect=fake_download):
        execute_movie_plan(plan, move=False)

    # Copy mode: sources remain, dests exist.
    assert video.exists()
    assert sub.exists()
    assert os.path.exists(p["dest"])
    assert os.path.exists(os.path.join(movie_dir, "Juno (2007).en.srt"))
    assert os.path.exists(os.path.join(movie_dir, "Juno (2007).jpg"))
    assert os.path.exists(os.path.join(movie_dir, "backdrop.jpg"))
    assert os.path.exists(os.path.join(movie_dir, "logo.png"))


def test_execute_movie_plan_move_mode(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    video = src_dir / "The.Matrix.1999.REMUX.mkv"
    video.write_text("movie")

    dest = tmp_path / "out"
    client = TMDBClient("key")

    def get(path, params):
        if path == "/search/movie":
            return {"results": [{"id": 7, "title": "The Matrix", "release_date": "1999-03-31", "overview": ""}]}
        return {"posters": [], "backdrops": [], "logos": []}

    with mock.patch.object(client, "_get", side_effect=get):
        plan = build_movie_plan([str(video)], str(dest), client)

    with mock.patch.object(movies, "_download", side_effect=AssertionError("no artwork expected")):
        execute_movie_plan(plan, move=True)

    assert not video.exists()
    assert os.path.exists(plan[0]["dest"])


def test_build_movie_plan_skip(tmp_path, capsys):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    video = src_dir / "Unknown.Thing.mkv"
    video.write_text("movie")

    client = TMDBClient("key")
    with mock.patch.object(client, "_get", return_value={"results": []}), \
         mock.patch("builtins.input", return_value="s"):
        plan = build_movie_plan([str(video)], str(tmp_path / "out"), client)

    assert plan == []
    assert "Skipping" in capsys.readouterr().out
