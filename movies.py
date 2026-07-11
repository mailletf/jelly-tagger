"""
Movie organizing logic for jelly-tagger.
-----------------------------------------
Scans a folder of movie files, guesses their title/year from the filename,
looks them up on TMDB, and lays them out the way Jellyfin expects:

    Movies/
        Title (Year) [tmdbid-12345]/
            Title (Year).mkv
            Title (Year).jpg   (poster)
            backdrop.jpg
            logo.png

Requires a TMDB API key (https://www.themoviedb.org/settings/api), passed
via --tmdb-api-key or the TMDB_API_KEY environment variable.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from jelly_tagger import sanitize

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}
SUBTITLE_EXTENSIONS = {".srt", ".sub"}

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"

YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")

# Words/tags commonly found in scene-release filenames that we strip out
# before treating whatever's left as the title.
JUNK_TAGS = re.compile(
    r"\b("
    r"1080p|720p|2160p|480p|4k|uhd|hdr|dv|"
    r"bluray|blu-ray|brrip|bdrip|webrip|web-dl|webdl|web|hdtv|dvdrip|"
    r"x264|x265|h264|h265|hevc|avc|aac|ac3|dts|"
    r"remux|extended|unrated|directors[.\s]?cut|proper|repack|limited|"
    r"multi|dubbed|subbed"
    r")\b",
    re.IGNORECASE,
)


def guess_title_year(filename: str):
    """Guess a movie title and release year from a messy filename."""
    name = os.path.splitext(filename)[0]
    name = name.replace(".", " ").replace("_", " ")

    year = None
    year_match = YEAR_RE.search(name)
    if year_match:
        year = int(year_match.group(1))
        # Title is everything before the year.
        name = name[: year_match.start()]
    else:
        # No year found; strip known junk tags and hope for the best.
        name = JUNK_TAGS.split(name)[0]

    name = re.sub(r"[\[\]\(\)]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -.")

    return name or os.path.splitext(filename)[0], year


def _best_image(images, prefer_lang=None):
    if not images:
        return None
    if prefer_lang is not None:
        lang_matches = [img for img in images if img.get("iso_639_1") == prefer_lang]
        if lang_matches:
            images = lang_matches
    images = sorted(images, key=lambda i: i.get("vote_count", 0), reverse=True)
    return TMDB_IMAGE_BASE + images[0]["file_path"]


class TMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict):
        query = dict(params)
        query["api_key"] = self.api_key
        url = f"{TMDB_API_BASE}{path}?{urllib.parse.urlencode(query)}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"TMDB API error ({e.code}) for {path}: {e.read().decode('utf-8', 'ignore')}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach TMDB API: {e}") from e

    def search_movie(self, title: str, year=None):
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = str(year)
        data = self._get("/search/movie", params)
        candidates = []
        for r in data.get("results", []):
            release_date = r.get("release_date") or ""
            candidates.append({
                "id": r["id"],
                "title": r.get("title") or r.get("original_title") or "",
                "year": int(release_date[:4]) if release_date[:4].isdigit() else None,
                "overview": r.get("overview") or "",
            })
        return candidates

    def get_images(self, tmdb_id: int):
        data = self._get(f"/movie/{tmdb_id}/images", {})

        poster = _best_image(data.get("posters", []))
        backdrop = _best_image(data.get("backdrops", []))
        logo = _best_image(data.get("logos", []), prefer_lang="en") or _best_image(data.get("logos", []))

        return {"poster": poster, "backdrop": backdrop, "logo": logo}

    def search_tv(self, name: str, year=None):
        params = {"query": name, "include_adult": "false"}
        if year:
            params["first_air_date_year"] = str(year)
        data = self._get("/search/tv", params)
        candidates = []
        for r in data.get("results", []):
            first_air_date = r.get("first_air_date") or ""
            candidates.append({
                "id": r["id"],
                "title": r.get("name") or r.get("original_name") or "",
                "year": int(first_air_date[:4]) if first_air_date[:4].isdigit() else None,
                "overview": r.get("overview") or "",
            })
        return candidates

    def get_tv_images(self, tv_id: int):
        data = self._get(f"/tv/{tv_id}/images", {})

        poster = _best_image(data.get("posters", []))
        backdrop = _best_image(data.get("backdrops", []))
        logo = _best_image(data.get("logos", []), prefer_lang="en") or _best_image(data.get("logos", []))

        return {"poster": poster, "backdrop": backdrop, "logo": logo}


def _print_candidates(candidates):
    for i, c in enumerate(candidates, start=1):
        year_str = c["year"] or "----"
        overview = (c["overview"][:80] + "...") if len(c["overview"]) > 80 else c["overview"]
        print(f"  [{i}] {c['title']} ({year_str}) [tmdbid-{c['id']}]  {overview}")


def resolve_interactive(header: str, search_fn, guessed_name, guessed_year, skip_prompt: str):
    """Shared interactive TMDB resolution loop.

    Auto-matches when exactly one candidate exactly matches the current search
    name (and year, when one is set). Otherwise prints a numbered candidate list
    and lets the user pick a number, enter a new free-text search term, or skip.
    Returns the chosen candidate dict, or None if the user chose to skip.
    """
    search_name, search_year = guessed_name, guessed_year

    while True:
        print(f"\n{header}")
        print(f"  guessed: \"{search_name}\"" + (f" ({search_year})" if search_year else ""))
        try:
            candidates = search_fn(search_name, search_year)
        except RuntimeError as e:
            print(f"  ERROR: {e}")
            candidates = []

        exact = [
            c for c in candidates
            if c["title"].strip().lower() == search_name.strip().lower()
            and (search_year is None or c["year"] == search_year)
        ]
        if len(exact) == 1:
            match = exact[0]
            print(f"  -> auto-matched: {match['title']} ({match['year']}) [tmdbid-{match['id']}]")
            return match

        if not candidates:
            print("  No TMDB matches found.")
        else:
            print("  Candidates:")
            _print_candidates(candidates)

        answer = input(skip_prompt).strip()

        if answer.lower() in ("s", "skip"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            return candidates[int(answer) - 1]
        if answer:
            search_name, search_year = answer, None
        # empty input: just re-search with the same term


def resolve_movie(video_path: str, tmdb_client: TMDBClient):
    """Interactively resolve a video file to a confirmed TMDB movie match.

    Returns a dict with id/title/year, or None if the user chose to skip it.
    """
    filename = os.path.basename(video_path)
    guessed_title, guessed_year = guess_title_year(filename)
    return resolve_interactive(
        filename,
        tmdb_client.search_movie,
        guessed_title,
        guessed_year,
        "  Pick a number, enter a new search term, or 's' to skip this file: ",
    )


def find_video_files(source_dir: str):
    video_files = []
    for root, _, files in os.walk(source_dir):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                video_files.append(os.path.join(root, f))
    return sorted(video_files)


def _find_sibling_subtitles(video_path: str):
    directory = os.path.dirname(video_path)
    base = os.path.splitext(os.path.basename(video_path))[0]
    subs = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return subs
    for entry in entries:
        stem, ext = os.path.splitext(entry)
        if ext.lower() in SUBTITLE_EXTENSIONS and stem.startswith(base):
            # Anything after the video's base name (e.g. a ".en" language
            # suffix) is preserved.
            suffix = stem[len(base):]
            subs.append((os.path.join(directory, entry), suffix, ext))
    return subs


def build_movie_plan(video_files, dest_dir: str, tmdb_client: TMDBClient):
    plan = []
    for video_path in video_files:
        match = resolve_movie(video_path, tmdb_client)
        if match is None:
            print(f"  Skipping {os.path.basename(video_path)}")
            continue

        title = match["title"]
        year = match["year"]
        tmdb_id = match["id"]

        folder_name = sanitize(f"{title} ({year})" if year else title) + f" [tmdbid-{tmdb_id}]"
        base_name = sanitize(f"{title} ({year})" if year else title)
        movie_dir = os.path.join(dest_dir, folder_name)

        ext = os.path.splitext(video_path)[1]
        dest_video = os.path.join(movie_dir, f"{base_name}{ext}")

        subtitles = []
        for sub_src, suffix, sub_ext in _find_sibling_subtitles(video_path):
            subtitles.append((sub_src, os.path.join(movie_dir, f"{base_name}{suffix}{sub_ext}")))

        try:
            artwork = tmdb_client.get_images(tmdb_id)
        except RuntimeError as e:
            print(f"  WARNING: could not fetch artwork for {title}: {e}")
            artwork = {"poster": None, "backdrop": None, "logo": None}

        artwork_files = {}
        if artwork.get("poster"):
            artwork_files[artwork["poster"]] = os.path.join(movie_dir, f"{base_name}.jpg")
        if artwork.get("backdrop"):
            artwork_files[artwork["backdrop"]] = os.path.join(movie_dir, "backdrop.jpg")
        if artwork.get("logo"):
            artwork_files[artwork["logo"]] = os.path.join(movie_dir, "logo.png")

        plan.append({
            "src": video_path,
            "dest": dest_video,
            "movie_dir": movie_dir,
            "title": title,
            "year": year,
            "tmdb_id": tmdb_id,
            "subtitles": subtitles,
            "artwork_files": artwork_files,
        })
    return plan


def print_movie_plan(plan):
    if not plan:
        print("No movies to organize.")
        return
    for p in plan:
        print(f"{p['title']} ({p['year']}) [tmdbid-{p['tmdb_id']}]  ->  {p['dest']}")
        for _, sub_dest in p["subtitles"]:
            print(f"    + subtitle: {os.path.basename(sub_dest)}")
        for kind_dest in p["artwork_files"].values():
            print(f"    + artwork: {os.path.basename(kind_dest)}")
    print(f"\n{len(plan)} movie(s) total.")


def _download(url: str, dest: str):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
        out.write(resp.read())


def execute_movie_plan(plan, move: bool):
    import shutil

    errors = []
    total = len(plan)
    for i, item in enumerate(plan, start=1):
        src = item["src"]
        dest = item["dest"]
        label = f"{item['title']} ({item['year']})"
        try:
            os.makedirs(item["movie_dir"], exist_ok=True)

            if move:
                shutil.move(src, dest)
            else:
                shutil.copy2(src, dest)
            print(f"[{i}/{total}] {'Moved' if move else 'Copied'}: {label} -> {dest}")

            for sub_src, sub_dest in item["subtitles"]:
                if move:
                    shutil.move(sub_src, sub_dest)
                else:
                    shutil.copy2(sub_src, sub_dest)
                print(f"    + subtitle: {os.path.basename(sub_dest)}")

            for url, art_dest in item["artwork_files"].items():
                try:
                    _download(url, art_dest)
                    print(f"    + artwork: {os.path.basename(art_dest)}")
                except Exception as e:
                    errors.append(f"{label}: artwork {os.path.basename(art_dest)}: {e}")
                    print(f"    ERROR downloading {os.path.basename(art_dest)}: {e}")

        except Exception as e:
            errors.append(f"{label}: {e}")
            print(f"[{i}/{total}] ERROR: {label}: {e}")

    print()
    if errors:
        print(f"Done with {len(errors)} error(s) out of {total} movie(s).")
    else:
        print(f"Done. Organized {total} movie(s) into {os.path.dirname(plan[0]['movie_dir'])}" if plan else "Done.")
