# jelly-tagger

A small command-line tool that organizes messy media folders into the
layout Jellyfin expects. It has two modes:

- **`--mode music`** (default) — scans MP3 files, reads their ID3 tags, and
  reorganizes them into an Artist/Album/Track structure.
- **`--mode movies`** — scans video files, looks each one up on
  [TMDB](https://www.themoviedb.org/), and lays it out with a `tmdbid` tag
  and artwork the way Jellyfin likes.
- **`--mode tv`** — scans episode files, groups them by show, looks each
  show up on TMDB once, and lays episodes out in `Season XX` folders with
  show-level artwork.

## Music mode

```
Music Library/
    Artist Name/
        Album Name/
            01 - Track Title.mp3
            02 - Another Track.mp3
```

It reads **ID3 tags** already embedded in your MP3s (artist, album, track
number, title) and uses them to rename/move things into the right place.
It doesn't edit tags or fetch missing metadata itself — your tags need to
be correct first (see [Getting your tags right](#getting-your-tags-right)
below). Files with missing tags fall back to "Unknown Artist" /
"Unknown Album" / the original filename, so nothing gets skipped.

## Movies mode

```
Movies/
    Juno (2007) [tmdbid-7326]/
        Juno (2007).mkv
        Juno (2007).jpg      (poster)
        backdrop.jpg
        logo.png
        Juno (2007).en.srt   (if a matching subtitle sits next to the source file)
```

For each video file found, jelly-tagger guesses a title/year from the
filename (stripping resolution/codec/release-group tags like `1080p`,
`BluRay`, `x264`, etc.), searches TMDB for a match, and:

- **auto-picks** the match if there's exactly one exact title+year hit
- if nothing matches, **automatically retries** with simpler queries
  (dropping a leading collection index like `03 `, keeping only the part
  before ` - `, then trimming trailing words) until candidates show up
- otherwise **prompts you interactively** with a numbered list of
  candidates — pick one, type a new search term, or `s` to skip that file

Your answers are saved to a `.jelly-tagger-cache.json` file in the source
folder as you go, so if you interrupt a long run (Ctrl+C) and restart,
already-confirmed matches and skips aren't asked again. Delete that file to
start fresh. (Note that no files are copied/moved until you approve the
final confirmation prompt — interrupting before that leaves the source
untouched.)

This confirmation step always runs (even with `--yes`, which only skips the
final "proceed with copy?" prompt), since a wrong TMDB match is hard to
notice after the fact.

Once confirmed, it downloads the poster, backdrop, and logo from TMDB and
copies/moves the video (and any matching `.srt`/`.sub` subtitles sitting
next to it) into `Title (Year) [tmdbid-ID]/`.

## TV mode

```
Shows/
    Breaking Bad (2008) [tmdbid-1396]/
        Season 01/
            Breaking Bad (2008) S01E01.mkv
            Breaking Bad (2008) S01E02.mkv
        Season 02/
            ...
        poster.jpg
        backdrop.jpg
        logo.png
```

Episodes are recognized by `S01E02`, `1x02`, or a bare episode number inside
a `Season X` folder (multi-episode files like `S01E01E02` use the first
episode number). The show name is guessed from the containing folder when
there is one, otherwise from the filename.

Episodes are **grouped by show** and the TMDB match is confirmed once per
show — same interactive picker as movies mode, and `s` skips that show's
episodes entirely. Files whose season/episode can't be parsed prompt you to
type an `SxxExx` manually or skip them. Artwork (`poster.jpg`,
`backdrop.jpg`, `logo.png`) is downloaded once into the show's root folder,
and sibling `.srt`/`.sub` subtitles are renamed alongside each episode.

### TMDB API key

Movies and TV modes need a free TMDB API key: create one at
https://www.themoviedb.org/settings/api, then either:

```bash
export TMDB_API_KEY=your_key_here
```

or pass it per-run with `--tmdb-api-key your_key_here`.

## Requirements

- Python 3.11+
- [mutagen](https://mutagen.readthedocs.io/) for reading ID3 tags (music mode)
- A TMDB API key (movies mode only) — no extra Python packages needed, it
  uses the standard library to talk to the TMDB API.

Install with:

```bash
pip install mutagen
```

Or if you're using Poetry (see `pyproject.toml`):

```bash
poetry install
```

## Usage

```bash
python3 jelly_tagger.py SOURCE_DIR DEST_DIR [--mode music|movies] [--move] [--yes] [--dry-run]
```

- `SOURCE_DIR` — folder containing your messy files (scanned recursively)
- `DEST_DIR` — your Jellyfin library folder (music library or Movies folder)

### Examples

Preview what would happen, without touching any files:

```bash
python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin --dry-run
```

Copy files into place, with a confirmation prompt:

```bash
python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin
```

Move files instead of copying, and skip the confirmation prompt:

```bash
python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin --move --yes
```

Organize movies (still prompts to confirm/pick each TMDB match):

```bash
python3 jelly_tagger.py ~/Downloads/movies ~/Media/Movies --mode movies --dry-run
python3 jelly_tagger.py ~/Downloads/movies ~/Media/Movies --mode movies --move
```

Organize TV shows (confirms each show once, then places all its episodes):

```bash
python3 jelly_tagger.py ~/Downloads/tv ~/Media/Shows --mode tv --dry-run
python3 jelly_tagger.py ~/Downloads/tv ~/Media/Shows --mode tv --move
```

## Options

| Flag                  | Description                                                        |
|------------------------|---------------------------------------------------------------------|
| `--mode music\|movies\|tv` | Library type to organize (default: `music`)                     |
| `--tmdb-api-key KEY`  | TMDB API key for movies/tv modes (or set `TMDB_API_KEY` env var)    |
| `--move`              | Move files instead of copying them (deletes originals)              |
| `--yes`, `-y`         | Skip the final copy/move confirmation prompt                        |
| `--dry-run`           | Print the plan only; don't touch any files                          |

## How it decides where a file goes

For each MP3, the tool reads:

- **Artist** → top-level folder
- **Album** → subfolder under the artist
- **Track number** → zero-padded prefix on the filename (e.g. `01 -`)
- **Title** → rest of the filename

If a tag is missing, it falls back to `Unknown Artist`, `Unknown Album`, or
the file's original name. Filenames are sanitized to remove characters that
aren't safe across filesystems (`<>:"/\|?*`).

If a destination file already exists (and isn't the same file), the tool
appends `(1)`, `(2)`, etc. rather than overwriting it.

## Getting your tags right

jelly-tagger is only as good as your ID3 tags. If your files have messy,
incomplete, or wrong tags, fix them before running this tool — otherwise
tracks will end up in the wrong folders.

Recommended taggers:

- **[MusicBrainz Picard](https://picard.musicbrainz.org/)** (free, cross-platform) —
  automatically identifies your files by audio fingerprint and fills in tags
  from the MusicBrainz database. Best choice for bulk tagging.
- **[Mp3tag](https://www.mp3tag.de/)** (free, Windows/Mac) — great for manual
  editing, bulk renaming, and fixing tags that Picard gets wrong.

A typical workflow:

1. Run Picard on your MP3s to get clean, accurate tags.
2. Run jelly-tagger with `--dry-run` to preview the result.
3. Run jelly-tagger without `--dry-run` to copy/move files into place.

## Notes

- Copy is the default; nothing is deleted from the source folder unless you
  pass `--move`.
