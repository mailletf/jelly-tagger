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

# Placeholders read_tags falls back to when a tag is missing. Music mode uses
# them to spot which fields an AcoustID lookup should fill in.
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


def sanitize(name: str) -> str:
    """Make a string safe to use as a file/folder name."""
    if not name:
        return ""
    name = INVALID_CHARS.sub("", name)
    name = name.strip().strip(".")
    return name or "Unknown"


def read_tags(filepath: str):
    """Read artist/album/title/track number from an MP3 file's ID3 tags.

    A missing tag comes back as "" rather than as its placeholder, so callers
    can tell "no tag" apart from "a tag that happens to read Unknown Artist"
    or "a title that happens to match the filename". build_plan applies the
    placeholders once it no longer needs that distinction.
    """
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

    artist = artist.strip() if artist else ""
    album = album.strip() if album else ""
    title = title.strip() if title else ""

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


def fill_from_fingerprint(filepath, artist, album, title, fingerprinter):
    """Fill in tags that are absent by identifying the audio itself.

    Only fields with no tag at all are replaced, so a tag that is present
    always wins over the fingerprint match — including a title that happens
    to equal the filename, and a band genuinely called "Unknown Artist".
    Files whose tags are all present are never sent to AcoustID.
    """
    if artist and album and title:
        return artist, album, title

    try:
        found = fingerprinter.lookup(filepath)
    except Exception as e:
        # Never let one unreadable file or one bad response kill the run.
        print(f"  WARNING: could not fingerprint {os.path.basename(filepath)}: {e}")
        return artist, album, title

    if not found:
        print(f"  No AcoustID match for {os.path.basename(filepath)}")
        return artist, album, title

    artist = artist or found["artist"]
    album = album or found["album"]
    title = title or found["title"]

    print(f"  Fingerprinted {os.path.basename(filepath)}: {artist} / {album} / {title}")
    return artist, album, title


def build_plan(mp3_files, dest_dir, fingerprinter=None):
    plan = []
    for filepath in mp3_files:
        artist, album, title, track = read_tags(filepath)
        if fingerprinter is not None:
            artist, album, title = fill_from_fingerprint(
                filepath, artist, album, title, fingerprinter
            )
        # Whatever is still unknown falls back to a placeholder / the filename.
        artist = artist or UNKNOWN_ARTIST
        album = album or UNKNOWN_ALBUM
        title = title or os.path.splitext(os.path.basename(filepath))[0]
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
    parser.add_argument(
        "--opensubtitles-api-key", default=os.environ.get("OPENSUBTITLES_API_KEY"),
        help="OpenSubtitles API key (movies/tv mode). Enables downloading subtitles "
             "matched to each file's content hash. Falls back to OPENSUBTITLES_API_KEY.",
    )
    parser.add_argument(
        "--opensubtitles-user", default=os.environ.get("OPENSUBTITLES_USER"),
        help="OpenSubtitles account username, for a higher daily download quota",
    )
    parser.add_argument(
        "--opensubtitles-password", default=os.environ.get("OPENSUBTITLES_PASSWORD"),
        help="OpenSubtitles account password. Prefer the OPENSUBTITLES_PASSWORD env var.",
    )
    parser.add_argument(
        "--sub-langs", default="en",
        help="Comma-separated subtitle languages to fetch, best first, e.g. 'en,fr' (default: en)",
    )
    parser.add_argument(
        "--acoustid-api-key", default=os.environ.get("ACOUSTID_API_KEY"),
        help="AcoustID API key (music mode). Fingerprints files with missing tags and "
             "fills them in. Requires Chromaprint's fpcalc. Falls back to ACOUSTID_API_KEY.",
    )
    parser.add_argument(
        "--fpcalc", default="fpcalc",
        help="Path to the Chromaprint fpcalc binary (default: found on PATH)",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Ignore and don't update .jelly-tagger-cache.json (movies/tv mode only)",
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


def make_subs_client(args):
    """Build an OpenSubtitles client, or None when no API key was given."""
    if not args.opensubtitles_api_key:
        return None

    langs = [lang.strip() for lang in args.sub_langs.split(",") if lang.strip()]
    if not langs:
        sys.exit("Error: --sub-langs is empty. Pass at least one language, e.g. --sub-langs en")

    if bool(args.opensubtitles_user) != bool(args.opensubtitles_password):
        print("WARNING: --opensubtitles-user and --opensubtitles-password must both be set "
              "to get the higher download quota; continuing anonymously.")

    import subtitles
    return subtitles.OpenSubtitlesClient(
        args.opensubtitles_api_key,
        langs=langs,
        username=args.opensubtitles_user,
        password=args.opensubtitles_password,
    )


def run_music_mode(args):
    mp3_files = find_mp3s(args.source)
    if not mp3_files:
        print("No MP3 files found in that folder.")
        return

    fingerprinter = None
    if args.acoustid_api_key:
        import fingerprint
        fingerprinter = fingerprint.AcoustIDClient(args.acoustid_api_key, fpcalc_bin=args.fpcalc)

    plan = build_plan(mp3_files, args.dest, fingerprinter=fingerprinter)
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

    video_files = movies.find_video_files(
        args.source,
        min_size_bytes=movies.MIN_MOVIE_SIZE_BYTES,
        skip_extras=True,
    )
    if not video_files:
        print("No movie files found in that folder.")
        return

    tmdb_client = movies.TMDBClient(args.tmdb_api_key, image_langs=args.image_langs.split(","))
    cache = movies.ResolutionCache(args.source, disabled=args.no_cache)
    subs_client = make_subs_client(args)
    plan = movies.build_movie_plan(
        video_files, args.dest, tmdb_client, cache=cache, subs_client=subs_client
    )
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

    movies.execute_movie_plan(
        plan, move=args.move, refresh_artwork=args.refresh_artwork, subs_client=subs_client
    )


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
    cache = movies.ResolutionCache(args.source, disabled=args.no_cache)
    subs_client = make_subs_client(args)
    plan = tv.build_tv_plan(
        episode_files, args.dest, tmdb_client, cache=cache, subs_client=subs_client
    )
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

    tv.execute_tv_plan(
        plan, move=args.move, refresh_artwork=args.refresh_artwork, subs_client=subs_client
    )


if __name__ == "__main__":
    main()
