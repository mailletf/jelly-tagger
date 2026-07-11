#!/usr/bin/env python3
"""
Jellyfin Library Organizer (CLI)
-----------------------------
Scans a folder of media files and reorganizes them into the folder
structure Jellyfin expects.

Music mode (default) reads MP3 ID3 tags:

    Music Library/
        Artist/
            Album/
                01 - Track Title.mp3

Movies mode looks up each video file on TMDB and lays it out as:

    Movies/
        Title (Year) [tmdbid-12345]/
            Title (Year).mkv
            Title (Year).jpg
            backdrop.jpg
            logo.png

TV mode looks up each show on TMDB, groups episodes by show, and lays them
out as:

    Shows/
        Show Name (2010) [tmdbid-1396]/
            Season 01/
                Show Name (2010) S01E01.mkv
                Show Name (2010) S01E02.mkv
            poster.jpg
            backdrop.jpg
            logo.png

Usage:
    python3 jelly_tagger.py SOURCE_DIR DEST_DIR [--mode music|movies|tv] [--move] [--yes] [--dry-run]

Examples:
    # Preview only, no confirmation prompt, doesn't touch any files
    python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin --dry-run

    # Copy files into place, asking for confirmation first
    python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin

    # Move files instead of copy, skip the confirmation prompt
    python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin --move --yes

    # Organize movies (requires a TMDB API key)
    TMDB_API_KEY=xxxx python3 jelly_tagger.py ~/Downloads/movies ~/Media/Movies --mode movies

Requires: mutagen (pip install mutagen)
Movies mode also requires a free TMDB API key: https://www.themoviedb.org/settings/api
"""

import argparse
import os
import re
import shutil
import sys

try:
    from mutagen import File as MutagenFile
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError
except ImportError:
    sys.exit("Missing dependency 'mutagen'. Install it with:\n\n    pip install mutagen\n")


INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize(name: str) -> str:
    """Make a string safe to use as a file/folder name."""
    if not name:
        return ""
    name = INVALID_CHARS.sub("", name)
    name = name.strip().strip(".")
    return name or "Unknown"


def read_tags(filepath: str):
    """Read artist/album/title/track number from an MP3 file's ID3 tags."""
    artist = album = title = ""
    track = ""
    try:
        audio = EasyID3(filepath)
        artist = audio.get("albumartist", [""])[0] or audio.get("artist", [""])[0]
        album = audio.get("album", [""])[0]
        title = audio.get("title", [""])[0]
        track_raw = audio.get("tracknumber", [""])[0]
        if track_raw:
            track = track_raw.split("/")[0].strip()
    except ID3NoHeaderError:
        pass
    except Exception:
        # Fall back to generic mutagen reading for non-ID3 tag formats
        try:
            audio = MutagenFile(filepath, easy=True)
            if audio and audio.tags:
                artist = audio.tags.get("albumartist", [""])[0] or audio.tags.get("artist", [""])[0]
                album = audio.tags.get("album", [""])[0]
                title = audio.tags.get("title", [""])[0]
                track_raw = audio.tags.get("tracknumber", [""])[0]
                if track_raw:
                    track = track_raw.split("/")[0].strip()
        except Exception:
            pass

    base = os.path.splitext(os.path.basename(filepath))[0]
    artist = artist.strip() if artist else "Unknown Artist"
    album = album.strip() if album else "Unknown Album"
    title = title.strip() if title else base

    if track:
        try:
            track = f"{int(track):02d}"
        except ValueError:
            track = ""

    return artist, album, title, track


def build_dest_path(dest_root: str, artist: str, album: str, title: str, track: str, ext: str):
    artist_dir = sanitize(artist)
    album_dir = sanitize(album)
    title_clean = sanitize(title)

    if track:
        filename = f"{track} - {title_clean}{ext}"
    else:
        filename = f"{title_clean}{ext}"

    return os.path.join(dest_root, artist_dir, album_dir, filename)


def find_mp3s(source_dir: str):
    mp3_files = []
    for root, _, files in os.walk(source_dir):
        for f in files:
            if f.lower().endswith(".mp3"):
                mp3_files.append(os.path.join(root, f))
    return sorted(mp3_files)


def build_plan(mp3_files, dest_dir):
    plan = []
    for filepath in mp3_files:
        artist, album, title, track = read_tags(filepath)
        ext = os.path.splitext(filepath)[1]
        dest_path = build_dest_path(dest_dir, artist, album, title, track, ext)
        plan.append({
            "src": filepath,
            "dest": dest_path,
            "artist": artist,
            "album": album,
            "title": title,
            "track": track,
        })
    return plan


def print_plan(plan):
    if not plan:
        print("No MP3 files found.")
        return
    width_artist = max(len(p["artist"]) for p in plan)
    width_album = max(len(p["album"]) for p in plan)
    for p in plan:
        print(f"{p['artist']:<{width_artist}}  |  {p['album']:<{width_album}}  |  {p['track'] or '--':<2}  ->  {p['dest']}")
    print(f"\n{len(plan)} file(s) total.")


def execute_plan(plan, move: bool):
    errors = []
    total = len(plan)
    for i, item in enumerate(plan, start=1):
        src = item["src"]
        dest = item["dest"]
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)

            final_dest = dest
            counter = 1
            while os.path.exists(final_dest) and os.path.abspath(final_dest) != os.path.abspath(src):
                base, ext = os.path.splitext(dest)
                final_dest = f"{base} ({counter}){ext}"
                counter += 1

            if move:
                shutil.move(src, final_dest)
            else:
                shutil.copy2(src, final_dest)

            print(f"[{i}/{total}] {'Moved' if move else 'Copied'}: {os.path.basename(src)} -> {final_dest}")
        except Exception as e:
            errors.append(f"{os.path.basename(src)}: {e}")
            print(f"[{i}/{total}] ERROR: {os.path.basename(src)}: {e}")

    print()
    if errors:
        print(f"Done with {len(errors)} error(s) out of {total} file(s).")
    else:
        print(f"Done. Organized {total} file(s) into {os.path.commonpath([p['dest'] for p in plan])}" if plan else "Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Organize MP3s or movies into a Jellyfin-friendly folder structure."
    )
    parser.add_argument("source", help="Folder containing files to organize (scanned recursively)")
    parser.add_argument("dest", help="Destination Jellyfin library folder")
    parser.add_argument(
        "--mode", choices=["music", "movies", "tv"], default="music",
        help="Library type to organize (default: music)",
    )
    parser.add_argument(
        "--tmdb-api-key", default=os.environ.get("TMDB_API_KEY"),
        help="TMDB API key (movies/tv mode only). Falls back to the TMDB_API_KEY env var.",
    )
    parser.add_argument(
        "--image-langs", default="en",
        help="Comma-separated language preference for TMDB posters/logos, e.g. 'en,fr' (default: en)",
    )
    parser.add_argument(
        "--refresh-artwork", action="store_true",
        help="Re-download artwork even if the image files already exist",
    )
    parser.add_argument("--move", action="store_true", help="Move files instead of copying (deletes originals)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan only, don't touch any files")
    args = parser.parse_args()

    if not os.path.isdir(args.source):
        sys.exit(f"Error: source folder does not exist: {args.source}")

    if args.mode == "movies":
        run_movies_mode(args)
    elif args.mode == "tv":
        run_tv_mode(args)
    else:
        run_music_mode(args)


def run_music_mode(args):
    mp3_files = find_mp3s(args.source)
    if not mp3_files:
        print("No MP3 files found in that folder.")
        return

    plan = build_plan(mp3_files, args.dest)
    print_plan(plan)

    if args.dry_run:
        print("\n(dry run — no files were touched)")
        return

    if not args.yes:
        action = "move" if args.move else "copy"
        answer = input(f"\n{action.capitalize()} these {len(plan)} file(s) into {args.dest}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    execute_plan(plan, move=args.move)


def run_movies_mode(args):
    if not args.tmdb_api_key:
        sys.exit(
            "Error: movies mode requires a TMDB API key.\n"
            "Pass --tmdb-api-key or set the TMDB_API_KEY environment variable.\n"
            "Get a free key at https://www.themoviedb.org/settings/api"
        )

    import movies

    video_files = movies.find_video_files(args.source)
    if not video_files:
        print("No movie files found in that folder.")
        return

    tmdb_client = movies.TMDBClient(args.tmdb_api_key, image_langs=args.image_langs.split(","))
    cache = movies.ResolutionCache(args.source)
    plan = movies.build_movie_plan(video_files, args.dest, tmdb_client, cache=cache)
    print()
    movies.print_movie_plan(plan)

    if not plan:
        return

    if args.dry_run:
        print("\n(dry run — no files were touched)")
        return

    if not args.yes:
        action = "move" if args.move else "copy"
        answer = input(f"\n{action.capitalize()} these {len(plan)} movie(s) into {args.dest}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    movies.execute_movie_plan(plan, move=args.move, refresh_artwork=args.refresh_artwork)


def run_tv_mode(args):
    if not args.tmdb_api_key:
        sys.exit(
            "Error: tv mode requires a TMDB API key.\n"
            "Pass --tmdb-api-key or set the TMDB_API_KEY environment variable.\n"
            "Get a free key at https://www.themoviedb.org/settings/api"
        )

    import movies
    import tv

    episode_files = tv.find_episode_files(args.source)
    if not episode_files:
        print("No episode files found in that folder.")
        return

    tmdb_client = movies.TMDBClient(args.tmdb_api_key, image_langs=args.image_langs.split(","))
    cache = movies.ResolutionCache(args.source)
    plan = tv.build_tv_plan(episode_files, args.dest, tmdb_client, cache=cache)
    print()
    tv.print_tv_plan(plan)

    if not plan:
        return

    if args.dry_run:
        print("\n(dry run — no files were touched)")
        return

    if not args.yes:
        action = "move" if args.move else "copy"
        answer = input(f"\n{action.capitalize()} these {len(plan)} episode(s) into {args.dest}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    tv.execute_tv_plan(plan, move=args.move, refresh_artwork=args.refresh_artwork)


if __name__ == "__main__":
    main()
