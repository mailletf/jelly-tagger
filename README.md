# jelly-tagger

A small command-line tool that organizes messy media folders into the
layout Jellyfin expects. It has three modes:

- **`--mode music`** (default) — scans audio files (`.mp3`, `.flac`, `.m4a`,
  `.m4b`, `.ogg`, `.oga`, `.opus`), reads their embedded tags, and
  reorganizes them into an Artist/Album/Track structure.
- **`--mode movies`** — scans video files, looks each one up on
  [TMDB](https://www.themoviedb.org/), and lays it out with a `tmdbid` tag
  and artwork the way Jellyfin likes.
- **`--mode tv`** — scans episode files, groups them by show, looks each
  show up on TMDB once, and lays episodes out in `Season XX` folders with
  show-level artwork.

It's a small, readable alternative to [FileBot](https://www.filebot.net/),
covering the parts most people actually use it for: matching messy filenames
against an online database, renaming into a media-server layout, fetching
artwork, downloading hash-matched subtitles, and identifying untagged music
by audio fingerprint. FileBot does considerably more — custom format
expressions, watch-folder automation, AniDB, archive verification — so if you
need those, use FileBot. If you want something you can read end to end in a
single sitting and change yourself, this is that.

## Music mode

```
Music Library/
    Artist Name/
        Album Name/
            01 - Track Title.mp3
            02 - Another Track.mp3
```

It reads the **tags** already embedded in your files (artist, album, track
number, title) and uses them to rename/move things into the right place.
Files with missing tags fall back to "Unknown Artist" / "Unknown Album" /
the original filename, so nothing gets skipped — or, with an AcoustID key,
get identified by audio fingerprint instead (see below).

### Identifying untagged files by fingerprint

Untagged files don't have to land in `Unknown Artist`. With an AcoustID API
key, jelly-tagger fingerprints what the file actually *sounds like* and looks
the recording up on MusicBrainz:

```bash
export ACOUSTID_API_KEY=your_key_here
python3 jelly_tagger.py ~/Downloads/messy-mp3s ~/Music/Jellyfin
```

This needs two things:

- A free API key from https://acoustid.org/new-application
- **Chromaprint's `fpcalc`** binary, which computes the fingerprint:
  `brew install chromaprint` (macOS) or `apt install libchromaprint-tools`
  (Debian/Ubuntu). Point at a specific binary with `--fpcalc /path/to/fpcalc`
  if it isn't on your `PATH`.

Only files with a genuinely *absent* tag are fingerprinted, and only the
absent fields get filled in — a tag that is present always wins, and fully
tagged files are never sent to AcoustID. That holds for the awkward cases
too: a file named `Karma Police.mp3` that is correctly tagged
`title=Karma Police` counts as tagged (it won't be renamed to some
remaster's title), and a band genuinely tagged `Unknown Artist` is left
alone. It doesn't write tags back to your files; it only uses the match to
decide where each file goes.

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

If two source files resolve to the same destination (e.g. two rips of the
same movie), nothing is overwritten: an existing file of the same size is
assumed to be the same copy and skipped, and a different file gets a
`(1)`-style suffix — Jellyfin shows those as multiple versions of the
movie. The same applies in TV mode.

Artwork is picked by language preference (`--image-langs`, default `en`)
before popularity, so a heavily-voted foreign poster doesn't win over a
sensible one in your language; backdrops prefer textless images.

### Refreshing artwork on an organized library

Files inside a `[tmdbid-N]` folder are recognized directly — no TMDB
search, no prompts. That means you can point the tool at your organized
library (same folder as source and destination) to re-fetch artwork:

```bash
python3 jelly_tagger.py ~/Media/Movies ~/Media/Movies --mode movies \
    --refresh-artwork --image-langs en,fr --yes
```

Videos are left untouched (already in place); missing artwork is filled in,
and with `--refresh-artwork` existing images are replaced with the current
best pick.

### Fixing a wrong match

Point the source at the mis-tagged folder, with your library as destination:

```bash
python3 jelly_tagger.py "~/Media/Movies/Wrong Title (2001) [tmdbid-123]" ~/Media/Movies \
    --mode movies --rematch
```

`--rematch` ignores the `[tmdbid-N]` tag and the cache and always shows the
candidate list (type a new search term if the right movie isn't there). The
video and its subtitles are moved into the new folder, then the old folder is
deleted if only artwork is left in it. Rescan the library in Jellyfin afterwards.

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

### Downloading subtitles

With an OpenSubtitles API key, movies and TV modes also fetch subtitles for
each file. Matching is done on the file's **OSDb hash** (its size plus its
first and last 64KB) rather than on the title, so the subtitle you get is
timed to that exact release instead of drifting out of sync.

```bash
export OPENSUBTITLES_API_KEY=your_key_here
python3 jelly_tagger.py ~/Downloads/movies ~/Media/Movies --mode movies --sub-langs en,fr
```

Get a free key at https://www.opensubtitles.com/consumers. Downloads are
quota-limited per day; passing account credentials raises that quota well
above the anonymous allowance:

```bash
export OPENSUBTITLES_USER=your_username
export OPENSUBTITLES_PASSWORD=your_password
```

Languages already sitting next to the source file are skipped, so a video
that ships with its own `.en.srt` won't spend quota re-downloading English.
Downloads happen only after you approve the final confirmation prompt, and
a subtitle already present at the destination is never re-fetched.

Note that building the plan **does** query the OpenSubtitles search API once
per file, including under `--dry-run`. Searching doesn't consume download
quota — only the actual download does, and that never happens before you
approve — but `--dry-run` is not a fully offline preview.

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
- A TMDB API key (movies/tv modes only)
- *Optional:* an OpenSubtitles API key to download subtitles (movies/tv modes)
- *Optional:* an AcoustID API key plus Chromaprint's `fpcalc` binary to
  identify untagged music (music mode)

No extra Python packages are needed for any of the API integrations — they
all talk to their services using the standard library.

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
| `--image-langs LANGS` | Comma-separated language preference for posters/logos, e.g. `en,fr` (default: `en`) |
| `--refresh-artwork`   | Re-download artwork even if the image files already exist           |
| `--opensubtitles-api-key KEY` | Enable subtitle downloads for movies/tv (or `OPENSUBTITLES_API_KEY`) |
| `--opensubtitles-user USER` | OpenSubtitles account username, for a higher daily download quota |
| `--opensubtitles-password PASS` | OpenSubtitles password (prefer the `OPENSUBTITLES_PASSWORD` env var) |
| `--sub-langs LANGS`   | Comma-separated subtitle languages, best first, e.g. `en,fr` (default: `en`) |
| `--acoustid-api-key KEY` | Fingerprint untagged music files to identify them (or `ACOUSTID_API_KEY`) |
| `--fpcalc PATH`       | Path to Chromaprint's `fpcalc` binary (default: found on `PATH`)    |
| `--no-cache`          | Ignore and don't update `.jelly-tagger-cache.json` (movies/tv modes) |
| `--move`              | Move files instead of copying them (deletes originals)              |
| `--yes`, `-y`         | Skip the final copy/move confirmation prompt                        |
| `--dry-run`           | Print the plan only; don't touch any files                          |

## How it decides where a file goes

For each audio file, the tool reads:

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

jelly-tagger is only as good as your ID3 tags. Files with *absent* tags can
be identified by fingerprint (see
[Identifying untagged files by fingerprint](#identifying-untagged-files-by-fingerprint)),
but files with *wrong* tags will be trusted and end up in the wrong folders —
fingerprinting deliberately never overrides a tag that's already there, so it
cannot rescue you from bad tags. Fix those before running this tool.

Recommended taggers:

- **[MusicBrainz Picard](https://picard.musicbrainz.org/)** (free, cross-platform) —
  automatically identifies your files by audio fingerprint and fills in tags
  from the MusicBrainz database. Best choice for bulk tagging.
- **[Mp3tag](https://www.mp3tag.de/)** (free, Windows/Mac) — great for manual
  editing, bulk renaming, and fixing tags that Picard gets wrong.

A typical workflow:

1. Run Picard on your files to get clean, accurate tags.
2. Run jelly-tagger with `--dry-run` to preview the result.
3. Run jelly-tagger without `--dry-run` to copy/move files into place.

## Notes

- Copy is the default; nothing is deleted from the source folder unless you
  pass `--move`.
