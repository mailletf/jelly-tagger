import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "jelly_tagger.py")


def run_cli(args, env_overrides=None):
    env = dict(os.environ)
    env.pop("TMDB_API_KEY", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_help_exits_zero_and_lists_modes():
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "music" in result.stdout
    assert "movies" in result.stdout
    assert "tv" in result.stdout


def test_tv_mode_without_key_errors(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    result = run_cli([str(src), str(tmp_path / "dest"), "--mode", "tv"])
    assert result.returncode != 0
    assert "themoviedb.org" in result.stderr


def test_movies_mode_without_key_errors(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    result = run_cli([str(src), str(tmp_path / "dest"), "--mode", "movies"])
    assert result.returncode != 0
    assert "themoviedb.org" in result.stderr


def test_missing_source_dir_errors(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = run_cli([str(missing), str(tmp_path / "dest")])
    assert result.returncode != 0
    assert "source folder does not exist" in result.stderr
