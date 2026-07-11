import os
from unittest import mock

import pytest

import movies
import tv


ARTWORK = {
    "poster": "https://img/poster.jpg",
    "backdrop": "https://img/backdrop.jpg",
    "logo": "https://img/logo.png",
}


def build_plan(tmp_path):
    show_dir = tmp_path / "Alpha (2001)" / "Season 01"
    show_dir.mkdir(parents=True)
    for ep in ("Alpha S01E01.mkv", "Alpha S01E02.mkv"):
        (show_dir / ep).write_text("content")
    paths = [str(show_dir / "Alpha S01E01.mkv"), str(show_dir / "Alpha S01E02.mkv")]
    eps = [(p, *tv.parse_episode(p)) for p in paths]

    client = mock.Mock()
    client.search_tv = mock.Mock(side_effect=lambda name, year: [
        {"id": 100, "title": name, "year": year, "overview": ""}
    ])
    client.get_tv_images = mock.Mock(return_value=dict(ARTWORK))
    dest = tmp_path / "out"
    plan = tv.build_tv_plan(eps, str(dest), client)
    return plan, str(dest)


def fake_download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(b"art")


def test_copy_mode(tmp_path, capsys):
    plan, dest = build_plan(tmp_path)
    with mock.patch.object(movies, "_download", side_effect=fake_download):
        tv.execute_tv_plan(plan, move=False)

    # Sources remain.
    for item in plan:
        assert os.path.exists(item["src"])
        assert os.path.exists(item["dest"])

    show_dir = plan[0]["show_dir"]
    assert os.path.exists(os.path.join(show_dir, "poster.jpg"))
    assert os.path.exists(os.path.join(show_dir, "backdrop.jpg"))
    assert os.path.exists(os.path.join(show_dir, "logo.png"))
    # Episodes land in correct Season folder.
    assert os.path.exists(os.path.join(show_dir, "Season 01", "Alpha (2001) S01E01.mkv"))


def test_move_mode(tmp_path):
    plan, dest = build_plan(tmp_path)
    with mock.patch.object(movies, "_download", side_effect=fake_download):
        tv.execute_tv_plan(plan, move=True)

    for item in plan:
        assert not os.path.exists(item["src"])
        assert os.path.exists(item["dest"])


def test_artwork_download_error_is_non_fatal(tmp_path, capsys):
    plan, dest = build_plan(tmp_path)

    def boom(url, dest):
        raise RuntimeError("network down")

    with mock.patch.object(movies, "_download", side_effect=boom):
        tv.execute_tv_plan(plan, move=False)

    # Video files still landed despite artwork failure.
    for item in plan:
        assert os.path.exists(item["dest"])

    out = capsys.readouterr().out
    assert "error" in out.lower()
