"""
TV show organizing logic for jelly-tagger.
------------------------------------------
Scans a folder of episode files, groups them by show, guesses the show
name/year from filenames (or folder names), looks the show up on TMDB, and
lays everything out the way Jellyfin expects:

    Shows/
        Show Name (2010) [tmdbid-1396]/
            Season 01/
                Show Name (2010) S01E01.mkv
                Show Name (2010) S01E02.mkv
            poster.jpg
            backdrop.jpg
            logo.png

Requires a TMDB API key (https://www.themoviedb.org/settings/api), passed
via --tmdb-api-key or the TMDB_API_KEY environment variable.
"""

import os
import re

import movies
from jelly_tagger import sanitize

# S01E02, including multi-episode forms (S01E02E03, S01E02-E03). We only take
# the first episode number.
SXXEXX_RE = re.compile(r"(?i)s(\d{1,2})\s*e(\d{1,3})")
# 1x02 style.
XFORMAT_RE = re.compile(r"(?i)(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)")
# "Season 1" / "Season 01" / "S1" / "S01" folder names.
SEASON_DIR_RE = re.compile(r"(?i)^(?:season|s)\s*0*(\d{1,2})$")
# A bare episode number in a filename (used with a season folder).
BARE_EP_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


def _match_token(text: str):
    """Find an SxxExx or 1x02 episode token; return (season, episode, start)."""
    m = SXXEXX_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), m.start()
    m = XFORMAT_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), m.start()
    return None


def parse_episode(path: str):
    """Guess (season, episode) for a file, or None if it can't be parsed."""
    stem = os.path.splitext(os.path.basename(path))[0]

    token = _match_token(stem)
    if token:
        return token[0], token[1]

    # Season-folder inference: a "Season X" parent plus a bare episode number.
    parent = os.path.basename(os.path.dirname(path))
    season_match = SEASON_DIR_RE.match(parent)
    if season_match:
        ep_match = BARE_EP_RE.search(stem)
        if ep_match:
            return int(season_match.group(1)), int(ep_match.group(1))

    return None


def _clean_name(text: str):
    """Normalize a raw show-name string, extracting a year if present.

    Mirrors movies.guess_title_year's cleanup (junk-tag stripping, dot/
    underscore normalization) but for show names.
    """
    name = text.replace(".", " ").replace("_", " ")

    year = None
    year_match = movies.YEAR_RE.search(name)
    if year_match:
        year = int(year_match.group(1))
        name = name[: year_match.start()]
    else:
        name = movies.JUNK_TAGS.split(name)[0]

    name = re.sub(r"[\[\]\(\)]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -.")

    return name or text, year


def guess_show_name(path: str, scan_root: str):
    """Guess (show_name, year_or_None) for an episode file.

    Prefers the containing folder's name (or the grandparent's when the file
    sits directly in a "Season X" folder) when that folder isn't the scan root;
    otherwise falls back to the filename with the episode token stripped off.
    """
    stem = os.path.splitext(os.path.basename(path))[0]

    token = _match_token(stem)
    base = stem[: token[2]] if token else stem

    parent_path = os.path.dirname(path)
    parent = os.path.basename(parent_path)
    if SEASON_DIR_RE.match(parent):
        folder_path = os.path.dirname(parent_path)
    else:
        folder_path = parent_path
    folder = os.path.basename(folder_path)

    if folder and os.path.abspath(folder_path) != os.path.abspath(scan_root):
        source = folder
    else:
        source = base

    return _clean_name(source)


def find_episode_files(source_dir: str):
    """Return a list of (path, season, episode) for parseable episode files.

    Video files whose season/episode can't be parsed are printed and the user
    is prompted to type an SxxExx manually or skip.
    """
    episode_files = []
    unparseable = []
    for video_path in movies.find_video_files(source_dir):
        parsed = parse_episode(video_path)
        if parsed:
            episode_files.append((video_path, parsed[0], parsed[1]))
        else:
            unparseable.append(video_path)

    for video_path in unparseable:
        print(f"\nCould not parse season/episode from: {os.path.basename(video_path)}")
        answer = input("  Enter SxxExx (e.g. S01E02), or 's'/enter to skip: ").strip()
        if not answer or answer.lower() in ("s", "skip"):
            print(f"  Skipping {os.path.basename(video_path)}")
            continue
        token = _match_token(answer)
        if token:
            episode_files.append((video_path, token[0], token[1]))
        else:
            print(f"  Could not parse \"{answer}\"; skipping {os.path.basename(video_path)}")

    return episode_files


def resolve_show(name_guess: str, year_guess, tmdb_client):
    """Interactively resolve a show-name guess to a confirmed TMDB match.

    Returns a dict with id/title/year, or None if the user chose to skip it.
    """
    header = name_guess + (f" ({year_guess})" if year_guess else "")
    return movies.resolve_interactive(
        header,
        tmdb_client.search_tv,
        name_guess,
        year_guess,
        "  Pick a number, enter a new search term, or 's' to skip this show: ",
    )


def build_tv_plan(episode_files, dest_dir: str, tmdb_client, cache=None):
    if not episode_files:
        return []

    scan_root = os.path.commonpath([os.path.dirname(p) for p, _, _ in episode_files])

    # Group episodes by normalized show-name guess.
    groups = {}
    order = []
    for video_path, season, episode in episode_files:
        name, year = guess_show_name(video_path, scan_root)
        key = name.casefold()
        if key not in groups:
            groups[key] = {"name": name, "year": year, "episodes": []}
            order.append(key)
        groups[key]["episodes"].append((video_path, season, episode))

    plan = []
    for key in order:
        group = groups[key]
        cache_key = f"tv:{key}"
        cached = cache.get(cache_key) if cache else None
        if cached is not None:
            if cached.get("skipped"):
                print(f"Skipping show \"{group['name']}\" (cached answer)")
                continue
            match = cached
            print(f"{group['name']}: cached match "
                  f"{match['title']} ({match['year']}) [tmdbid-{match['id']}]")
        else:
            match = resolve_show(group["name"], group["year"], tmdb_client)
            if cache:
                cache.set(cache_key, match if match else {"skipped": True})
        if match is None:
            print(f"  Skipping show \"{group['name']}\" ({len(group['episodes'])} episode(s))")
            continue

        title = match["title"]
        year = match["year"]
        tmdb_id = match["id"]

        folder_name = sanitize(f"{title} ({year})" if year else title) + f" [tmdbid-{tmdb_id}]"
        base_name = sanitize(f"{title} ({year})" if year else title)
        show_dir = os.path.join(dest_dir, folder_name)

        try:
            artwork = tmdb_client.get_tv_images(tmdb_id)
        except RuntimeError as e:
            print(f"  WARNING: could not fetch artwork for {title}: {e}")
            artwork = {"poster": None, "backdrop": None, "logo": None}

        artwork_files = {}
        if artwork.get("poster"):
            artwork_files[artwork["poster"]] = os.path.join(show_dir, "poster.jpg")
        if artwork.get("backdrop"):
            artwork_files[artwork["backdrop"]] = os.path.join(show_dir, "backdrop.jpg")
        if artwork.get("logo"):
            artwork_files[artwork["logo"]] = os.path.join(show_dir, "logo.png")

        episodes = sorted(group["episodes"], key=lambda e: (e[1], e[2]))
        for idx, (video_path, season, episode) in enumerate(episodes):
            season_dir = os.path.join(show_dir, f"Season {season:02d}")
            ep_base = f"{base_name} S{season:02d}E{episode:02d}"
            ext = os.path.splitext(video_path)[1]
            dest_video = os.path.join(season_dir, f"{ep_base}{ext}")

            subtitles = []
            for sub_src, suffix, sub_ext in movies._find_sibling_subtitles(video_path):
                subtitles.append((sub_src, os.path.join(season_dir, f"{ep_base}{suffix}{sub_ext}")))

            plan.append({
                "src": video_path,
                "dest": dest_video,
                "show": title,
                "year": year,
                "tmdb_id": tmdb_id,
                "season": season,
                "episode": episode,
                "show_dir": show_dir,
                "season_dir": season_dir,
                "subtitles": subtitles,
                # Artwork is fetched once per show and attached to its first
                # episode so the execute loop can stay flat.
                "artwork_files": artwork_files if idx == 0 else {},
            })
    return plan


def print_tv_plan(plan):
    if not plan:
        print("No episodes to organize.")
        return
    current = None
    for p in plan:
        if p["tmdb_id"] != current:
            current = p["tmdb_id"]
            year_str = f" ({p['year']})" if p["year"] else ""
            print(f"{p['show']}{year_str} [tmdbid-{p['tmdb_id']}]")
        print(f"    S{p['season']:02d}E{p['episode']:02d}  ->  {p['dest']}")
        for _, sub_dest in p["subtitles"]:
            print(f"        + subtitle: {os.path.basename(sub_dest)}")
        for art_dest in p["artwork_files"].values():
            print(f"        + artwork: {os.path.basename(art_dest)}")
    print(f"\n{len(plan)} episode(s) total.")


def execute_tv_plan(plan, move: bool):
    import shutil

    errors = []
    total = len(plan)
    for i, item in enumerate(plan, start=1):
        src = item["src"]
        dest = item["dest"]
        label = f"{item['show']} S{item['season']:02d}E{item['episode']:02d}"
        try:
            os.makedirs(item["season_dir"], exist_ok=True)

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
                    movies._download(url, art_dest)
                    print(f"    + artwork: {os.path.basename(art_dest)}")
                except Exception as e:
                    errors.append(f"{label}: artwork {os.path.basename(art_dest)}: {e}")
                    print(f"    ERROR downloading {os.path.basename(art_dest)}: {e}")

        except Exception as e:
            errors.append(f"{label}: {e}")
            print(f"[{i}/{total}] ERROR: {label}: {e}")

    print()
    if errors:
        print(f"Done with {len(errors)} error(s) out of {total} episode(s).")
    else:
        print(f"Done. Organized {total} episode(s) into {os.path.dirname(plan[0]['show_dir'])}" if plan else "Done.")
