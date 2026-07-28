"""
Audio fingerprinting for jelly-tagger's music mode.
---------------------------------------------------
Identifies a track by what it *sounds like* rather than by its tags, so files
with missing or wrong ID3 tags still land in the right Artist/Album folder.

Chromaprint's `fpcalc` computes the fingerprint; AcoustID maps it to a
MusicBrainz recording. Both are needed:

    brew install chromaprint          (or: apt install libchromaprint-tools)
    free API key: https://acoustid.org/new-application
"""

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"


def fpcalc(path: str, fpcalc_bin: str = "fpcalc"):
    """Return (duration_seconds, fingerprint) for an audio file."""
    try:
        proc = subprocess.run(
            [fpcalc_bin, "-json", path],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"'{fpcalc_bin}' not found. Install Chromaprint:\n"
            "    brew install chromaprint      (macOS)\n"
            "    apt install libchromaprint-tools   (Debian/Ubuntu)"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"fpcalc timed out on {path}") from None
    except OSError as e:
        # e.g. --fpcalc points at a directory or a non-executable file.
        raise RuntimeError(f"Could not run '{fpcalc_bin}': {e}") from e

    if proc.returncode != 0:
        raise RuntimeError(f"fpcalc failed on {path}: {proc.stderr.strip()}")

    try:
        data = json.loads(proc.stdout)
        return int(data["duration"]), data["fingerprint"]
    except (ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"Could not parse fpcalc output for {path}: {e}") from e


def _pick_album(releasegroups):
    """Prefer a proper album over a compilation/soundtrack when both exist."""
    for group in releasegroups:
        if (group.get("type") or "") == "Album" and "Compilation" not in (
            group.get("secondarytypes") or []
        ):
            return group.get("title") or ""
    return (releasegroups[0].get("title") or "") if releasegroups else ""


def best_recording(results):
    """Pick artist/album/title from an AcoustID lookup's results.

    Results come back scored by fingerprint similarity; the first one with a
    usable recording title wins. Returns None when nothing is identifiable.
    """
    for result in sorted(results, key=lambda r: r.get("score") or 0, reverse=True):
        for recording in result.get("recordings") or []:
            title = recording.get("title")
            if not title:
                continue
            artists = recording.get("artists") or []
            return {
                "artist": (artists[0].get("name") or "") if artists else "",
                "album": _pick_album(recording.get("releasegroups") or []),
                "title": title,
            }
    return None


class AcoustIDClient:
    def __init__(self, api_key: str, fpcalc_bin: str = "fpcalc"):
        self.api_key = api_key
        self.fpcalc_bin = fpcalc_bin

    def _post(self, params: dict):
        # Fingerprints are long enough to blow past URL length limits, so the
        # lookup goes out as a form-encoded POST body.
        data = urllib.parse.urlencode(params).encode("utf-8")
        try:
            with urllib.request.urlopen(ACOUSTID_LOOKUP_URL, data=data, timeout=20) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise RuntimeError(f"AcoustID API error ({e.code}): {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Could not reach AcoustID API: {e}") from e

        try:
            return json.loads(body)
        except ValueError as e:
            # A proxy or captive portal answering 200 with HTML, typically.
            raise RuntimeError(f"AcoustID returned a non-JSON response: {e}") from e

    def lookup(self, path: str):
        """Identify an audio file. Returns {artist, album, title} or None."""
        duration, fingerprint = fpcalc(path, self.fpcalc_bin)
        data = self._post({
            "client": self.api_key,
            "meta": "recordings+releasegroups",
            "duration": duration,
            "fingerprint": fingerprint,
        })
        if data.get("status") != "ok":
            error = (data.get("error") or {}).get("message", "unknown error")
            raise RuntimeError(f"AcoustID lookup failed: {error}")
        return best_recording(data.get("results") or [])
