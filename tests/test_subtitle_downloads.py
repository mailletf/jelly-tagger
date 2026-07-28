import argparse
import os
from unittest import mock

import pytest

import jelly_tagger
import movies
import tv
from movies import TMDBClient


class FakeSubsClient:
    """Stands in for OpenSubtitlesClient without touching the network."""

    def __init__(self, found=(), search_error=None, download_error=None):
        self.found = list(found)
        self.search_error = search_error
        self.download_error = download_error
        self.searched = []
        self.downloaded = []

    def find_for_file(self, video_path):
        self.searched.append(video_path)
        if self.search_error:
            raise self.search_error
        return self.found

    def download_link(self, file_id):
        self.downloaded.append(file_id)
        if self.download_error:
            raise self.download_error
        return f"https://dl.example/{file_id}.srt"


def sub(language, file_id, release=""):
    return {"language": language, "file_id": file_id, "release": release}


def fake_tmdb_get(path, params):
    if path == "/search/movie":
        return {"results": [
            {"id": 123, "title": "Juno", "release_date": "2007-12-05", "overview": ""},
        ]}
    if path.endswith("/images"):
        return {"posters": [], "backdrops": [], "logos": []}
    if path == "/search/tv":
        return {"results": [
            {"id": 55, "name": "Breaking Bad", "first_air_date": "2008-01-20", "overview": ""},
        ]}
    raise AssertionError(f"unexpected path {path}")


def fake_download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(b"subtitle text")


# ---------------------------------------------------------------------------
# make_subs_client — flags that used to fail silently
# ---------------------------------------------------------------------------

def subs_args(**overrides):
    args = {
        "opensubtitles_api_key": "key", "sub_langs": "en",
        "opensubtitles_user": None, "opensubtitles_password": None,
    }
    args.update(overrides)
    return argparse.Namespace(**args)


def test_no_api_key_means_no_client():
    assert jelly_tagger.make_subs_client(subs_args(opensubtitles_api_key=None)) is None


def test_empty_sub_langs_is_an_error_not_a_silent_no_op():
    # Previously this searched once per file and always returned nothing.
    with pytest.raises(SystemExit, match="--sub-langs"):
        jelly_tagger.make_subs_client(subs_args(sub_langs="  ,  "))


def test_half_supplied_credentials_warn_instead_of_silently_downgrading(capsys):
    client = jelly_tagger.make_subs_client(subs_args(opensubtitles_user="me"))
    assert "continuing anonymously" in capsys.readouterr().out
    assert client is not None


def test_blank_langs_are_dropped_and_case_normalized():
    client = jelly_tagger.make_subs_client(subs_args(sub_langs="EN,,fr"))
    assert client.langs == ("en", "fr")


# ---------------------------------------------------------------------------
# plan_subtitle_downloads
# ---------------------------------------------------------------------------

def test_no_client_means_no_downloads(tmp_path):
    assert movies.plan_subtitle_downloads("/v.mkv", "/dest", "Base", None, "label") == []


def test_plans_one_download_per_language(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_text("x")
    client = FakeSubsClient([sub("en", 1, "GROUP"), sub("fr", 2)])

    planned = movies.plan_subtitle_downloads(str(video), "/dest", "Juno (2007)", client, "Juno")

    assert planned == [
        (1, os.path.join("/dest", "Juno (2007).en.srt"), "GROUP"),
        (2, os.path.join("/dest", "Juno (2007).fr.srt"), ""),
    ]


def test_skips_languages_already_present_next_to_the_video(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_text("x")
    (tmp_path / "v.en.srt").write_text("existing subs")
    client = FakeSubsClient([sub("en", 1), sub("fr", 2)])

    planned = movies.plan_subtitle_downloads(str(video), "/dest", "Base", client, "label")

    assert [file_id for file_id, _, _ in planned] == [2]


def test_search_failure_is_a_warning_not_a_crash(tmp_path, capsys):
    video = tmp_path / "v.mkv"
    video.write_text("x")
    client = FakeSubsClient(search_error=RuntimeError("API down"))

    assert movies.plan_subtitle_downloads(str(video), "/dest", "Base", client, "Juno") == []
    assert "API down" in capsys.readouterr().out


def test_os_errors_while_hashing_do_not_abort_the_run(tmp_path, capsys):
    # A video deleted or unmounted between scanning and planning raises
    # OSError from inside find_for_file; it must degrade to a warning.
    client = FakeSubsClient(search_error=FileNotFoundError("gone"))
    assert movies.plan_subtitle_downloads("/gone.mkv", "/dest", "Base", client, "Juno") == []
    assert "subtitle search failed" in capsys.readouterr().out


def test_forced_and_sdh_siblings_count_as_having_that_language(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_text("x")
    (tmp_path / "v.en.forced.srt").write_text("existing")
    client = FakeSubsClient([sub("en", 1), sub("fr", 2)])

    planned = movies.plan_subtitle_downloads(str(video), "/dest", "Base", client, "label")

    assert [file_id for file_id, _, _ in planned] == [2]


def test_already_downloaded_subtitle_is_not_advertised_again(tmp_path):
    # The plan must not promise a download the execute step will skip.
    video = tmp_path / "v.mkv"
    video.write_text("x")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "Juno (2007).en.srt").write_text("from an earlier run")
    client = FakeSubsClient([sub("en", 1), sub("fr", 2)])

    planned = movies.plan_subtitle_downloads(
        str(video), str(dest_dir), "Juno (2007)", client, "Juno"
    )

    assert [file_id for file_id, _, _ in planned] == [2]


# ---------------------------------------------------------------------------
# _download_subtitles (execute side)
# ---------------------------------------------------------------------------

def test_download_subtitles_writes_files(tmp_path):
    dest = tmp_path / "Juno (2007).en.srt"
    item = {"fetch_subtitles": [(7, str(dest), "")]}
    client = FakeSubsClient()
    errors = []

    with mock.patch.object(movies, "_download", side_effect=fake_download):
        movies._download_subtitles(item, "Juno", errors, client)

    assert client.downloaded == [7]
    assert dest.exists()
    assert errors == []


def test_download_subtitles_skips_existing_file_and_spends_no_quota(tmp_path):
    dest = tmp_path / "Juno (2007).en.srt"
    dest.write_text("already here")
    item = {"fetch_subtitles": [(7, str(dest), "")]}
    client = FakeSubsClient()

    with mock.patch.object(movies, "_download", side_effect=AssertionError("no download")):
        movies._download_subtitles(item, "Juno", [], client)

    assert client.downloaded == []


def test_download_subtitles_collects_errors_without_raising(tmp_path):
    dest = tmp_path / "Juno (2007).en.srt"
    item = {"fetch_subtitles": [(7, str(dest), "")]}
    client = FakeSubsClient(download_error=RuntimeError("quota exceeded"))
    errors = []

    movies._download_subtitles(item, "Juno", errors, client)

    assert len(errors) == 1
    assert "quota exceeded" in errors[0]
    assert not dest.exists()


# ---------------------------------------------------------------------------
# retarget_subtitle — subtitles must follow a video that got a "(1)" suffix
# ---------------------------------------------------------------------------

def test_retarget_is_a_no_op_without_a_collision():
    dest = os.path.join("/d", "Juno (2007).mkv")
    sub_dest = os.path.join("/d", "Juno (2007).en.srt")
    assert movies.retarget_subtitle(sub_dest, dest, dest) == sub_dest


def test_retarget_follows_the_renamed_video():
    dest = os.path.join("/d", "Juno (2007).mkv")
    final = os.path.join("/d", "Juno (2007) (1).mkv")
    sub_dest = os.path.join("/d", "Juno (2007).en.srt")
    assert movies.retarget_subtitle(sub_dest, dest, final) == os.path.join(
        "/d", "Juno (2007) (1).en.srt"
    )


def test_two_releases_of_one_movie_keep_their_own_subtitles(tmp_path):
    """A second rip must not steal or overwrite the first one's subtitle."""
    movie_dir = tmp_path / "Juno (2007) [tmdbid-123]"
    movie_dir.mkdir()
    # An earlier, different rip is already organized here.
    (movie_dir / "Juno (2007).mkv").write_text("the first rip, longer")
    (movie_dir / "Juno (2007).en.srt").write_text("subtitle for the first rip")

    src = tmp_path / "src"
    src.mkdir()
    video = src / "Juno.2007.REMUX.mkv"
    video.write_text("second rip")

    item = {
        "src": str(video),
        "dest": str(movie_dir / "Juno (2007).mkv"),
        "movie_dir": str(movie_dir),
        "title": "Juno", "year": 2007, "tmdb_id": 123,
        "subtitles": [],
        "fetch_subtitles": [(9, str(movie_dir / "Juno (2007).en.srt"), "REMUX")],
        "artwork_files": {},
    }
    client = FakeSubsClient()

    with mock.patch.object(movies, "_download", side_effect=fake_download):
        movies.execute_movie_plan([item], move=False, subs_client=client)

    # The second rip landed under a suffixed name, and its subtitle followed it.
    assert (movie_dir / "Juno (2007) (1).mkv").exists()
    assert (movie_dir / "Juno (2007) (1).en.srt").exists()
    # The first rip's subtitle is untouched.
    assert (movie_dir / "Juno (2007).en.srt").read_text() == "subtitle for the first rip"


def test_sibling_subtitle_does_not_overwrite_an_existing_one(tmp_path):
    """The same collision, for subtitles copied from next to the source."""
    movie_dir = tmp_path / "Juno (2007) [tmdbid-123]"
    movie_dir.mkdir()
    (movie_dir / "Juno (2007).mkv").write_text("the first rip, longer")
    (movie_dir / "Juno (2007).en.srt").write_text("subtitle for the first rip")

    src = tmp_path / "src"
    src.mkdir()
    video = src / "Juno.2007.REMUX.mkv"
    video.write_text("second rip")
    sibling = src / "Juno.2007.REMUX.en.srt"
    sibling.write_text("subtitle for the second rip")

    item = {
        "src": str(video),
        "dest": str(movie_dir / "Juno (2007).mkv"),
        "movie_dir": str(movie_dir),
        "title": "Juno", "year": 2007, "tmdb_id": 123,
        "subtitles": [(str(sibling), str(movie_dir / "Juno (2007).en.srt"))],
        "fetch_subtitles": [],
        "artwork_files": {},
    }

    movies.execute_movie_plan([item], move=False)

    assert (movie_dir / "Juno (2007) (1).en.srt").read_text() == "subtitle for the second rip"
    assert (movie_dir / "Juno (2007).en.srt").read_text() == "subtitle for the first rip"


# ---------------------------------------------------------------------------
# End-to-end through the movie and TV plans
# ---------------------------------------------------------------------------

def test_movie_plan_downloads_subtitles(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    video = src_dir / "Juno.2007.1080p.BluRay.x264-GROUP.mkv"
    video.write_text("movie")
    dest = tmp_path / "out"

    tmdb = TMDBClient("key")
    subs = FakeSubsClient([sub("en", 1), sub("fr", 2)])

    with mock.patch.object(tmdb, "_get", side_effect=fake_tmdb_get), \
         mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        plan = movies.build_movie_plan([str(video)], str(dest), tmdb, subs_client=subs)

    movie_dir = os.path.join(str(dest), "Juno (2007) [tmdbid-123]")
    assert [d for _, d, _ in plan[0]["fetch_subtitles"]] == [
        os.path.join(movie_dir, "Juno (2007).en.srt"),
        os.path.join(movie_dir, "Juno (2007).fr.srt"),
    ]

    with mock.patch.object(movies, "_download", side_effect=fake_download):
        movies.execute_movie_plan(plan, move=False, subs_client=subs)

    assert os.path.exists(os.path.join(movie_dir, "Juno (2007).en.srt"))
    assert os.path.exists(os.path.join(movie_dir, "Juno (2007).fr.srt"))


def test_movie_plan_without_subs_client_fetches_nothing(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    video = src_dir / "Juno.2007.1080p.BluRay.x264-GROUP.mkv"
    video.write_text("movie")

    tmdb = TMDBClient("key")
    with mock.patch.object(tmdb, "_get", side_effect=fake_tmdb_get), \
         mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        plan = movies.build_movie_plan([str(video)], str(tmp_path / "out"), tmdb)

    assert plan[0]["fetch_subtitles"] == []


def test_tv_plan_downloads_subtitles_per_episode(tmp_path):
    src_dir = tmp_path / "src" / "Breaking Bad"
    src_dir.mkdir(parents=True)
    ep1 = src_dir / "Breaking.Bad.S01E01.mkv"
    ep2 = src_dir / "Breaking.Bad.S01E02.mkv"
    ep1.write_text("ep1")
    ep2.write_text("ep2")
    dest = tmp_path / "out"

    tmdb = TMDBClient("key")
    subs = FakeSubsClient([sub("en", 1)])
    episodes = [(str(ep1), 1, 1), (str(ep2), 1, 2)]

    with mock.patch.object(tmdb, "_get", side_effect=fake_tmdb_get), \
         mock.patch("builtins.input", side_effect=AssertionError("should not prompt")):
        plan = tv.build_tv_plan(episodes, str(dest), tmdb, subs_client=subs)

    season_dir = os.path.join(
        str(dest), "Breaking Bad (2008) [tmdbid-55]", "Season 01"
    )
    assert [d for _, d, _ in plan[0]["fetch_subtitles"]] == [
        os.path.join(season_dir, "Breaking Bad (2008) S01E01.en.srt")
    ]
    assert [d for _, d, _ in plan[1]["fetch_subtitles"]] == [
        os.path.join(season_dir, "Breaking Bad (2008) S01E02.en.srt")
    ]

    with mock.patch.object(movies, "_download", side_effect=fake_download):
        tv.execute_tv_plan(plan, move=False, subs_client=subs)

    assert os.path.exists(os.path.join(season_dir, "Breaking Bad (2008) S01E01.en.srt"))
    assert os.path.exists(os.path.join(season_dir, "Breaking Bad (2008) S01E02.en.srt"))
