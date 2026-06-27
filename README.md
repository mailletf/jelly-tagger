# jelly-tagger

A small command-line tool that scans a folder of MP3 files, reads their ID3
tags, and reorganizes them into the folder structure Jellyfin expects for
its music library:

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

## Requirements

- Python 3.11+
- [mutagen](https://mutagen.readthedocs.io/) for reading ID3 tags

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
python3 jelly_tagger.py SOURCE_DIR DEST_DIR [--move] [--yes] [--dry-run]
```

- `SOURCE_DIR` — folder containing your messy MP3s (scanned recursively)
- `DEST_DIR` — your Jellyfin music library folder

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

## Options

| Flag           | Description                                              |
|-----------------|-----------------------------------------------------------|
| `--move`       | Move files instead of copying them (deletes originals)   |
| `--yes`, `-y`  | Skip the confirmation prompt                              |
| `--dry-run`    | Print the plan only; don't touch any files                |

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
