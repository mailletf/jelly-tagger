"""
OpenSubtitles lookup for jelly-tagger.
--------------------------------------
Finds subtitles for a video file by its OSDb hash — the same content hash
OpenSubtitles indexes on, computed from the file's size plus its first and
last 64KB. Matching on the hash rather than the title means the subtitle is
timed to that exact release, so it doesn't drift out of sync.

Requires a free OpenSubtitles API key (https://www.opensubtitles.com/consumers).
Downloads are quota-limited; passing an account username/password raises the
daily allowance well above the anonymous one.
"""

import json
import os
import struct
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.opensubtitles.com/api/v1"
# OpenSubtitles requires a descriptive User-Agent and rejects generic ones.
USER_AGENT = "jelly-tagger v0.1.0"

# The OSDb hash reads this many bytes from each end of the file.
CHUNK_SIZE = 64 * 1024


def compute_osdb_hash(path: str):
    """Return (hash_hex, size) for a video file, or (None, size) if too small.

    The OSDb hash is the file size plus every 8-byte little-endian chunk of
    the first and last 64KB, summed modulo 2**64. Files under 128KB have no
    defined hash.
    """
    size = os.path.getsize(path)
    if size < CHUNK_SIZE * 2:
        return None, size

    fmt = f"<{CHUNK_SIZE // 8}Q"
    with open(path, "rb") as f:
        head = f.read(CHUNK_SIZE)
        f.seek(-CHUNK_SIZE, os.SEEK_END)
        tail = f.read(CHUNK_SIZE)

    value = size
    for chunk in (head, tail):
        for word in struct.unpack(fmt, chunk):
            value = (value + word) % (1 << 64)

    return f"{value:016x}", size


def _rank(attributes):
    """Sort key for competing subtitles: exact-release match beats popularity."""
    return (
        bool(attributes.get("moviehash_match")),
        attributes.get("download_count") or 0,
    )


def best_per_language(results, langs):
    """Pick the single best subtitle for each requested language.

    Returns them in the order the languages were requested, so the caller's
    preference order is preserved.
    """
    best = {}
    for result in results:
        attributes = result.get("attributes") or {}
        language = (attributes.get("language") or "").lower()
        files = attributes.get("files") or []
        if not language or not files or not files[0].get("file_id"):
            continue
        candidate = {
            "language": language,
            "file_id": files[0]["file_id"],
            "release": attributes.get("release") or "",
            "_rank": _rank(attributes),
        }
        if language not in best or candidate["_rank"] > best[language]["_rank"]:
            best[language] = candidate

    return [best[lang] for lang in langs if lang in best]


class OpenSubtitlesClient:
    def __init__(self, api_key: str, langs=("en",), username=None, password=None):
        self.api_key = api_key
        self.langs = tuple(lang.strip().lower() for lang in langs if lang.strip())
        self.username = username
        self.password = password
        self._token = None
        self._login_failed = False

    def _request(self, path: str, params=None, payload=None):
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {
            "Api-Key": self.api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            raise RuntimeError(f"OpenSubtitles API error ({e.code}) for {path}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach OpenSubtitles API: {e}") from e

    def _login(self):
        """Exchange username/password for a token, once, if credentials were given.

        A failed login is remembered: retrying bad credentials once per file
        across a large library is a good way to get the account banned.
        """
        if self._token or not (self.username and self.password):
            return
        if self._login_failed:
            raise RuntimeError("OpenSubtitles login failed earlier; not retrying")

        self._login_failed = True
        data = self._request(
            "/login", payload={"username": self.username, "password": self.password}
        )
        self._token = data.get("token")
        if not self._token:
            raise RuntimeError("OpenSubtitles login did not return a token")
        self._login_failed = False

    def find_for_file(self, video_path: str):
        """Return the best subtitle per requested language for a video file."""
        file_hash, _size = compute_osdb_hash(video_path)
        if not file_hash:
            return []
        data = self._request(
            "/subtitles",
            params={"moviehash": file_hash, "languages": ",".join(self.langs)},
        )
        return best_per_language(data.get("data") or [], self.langs)

    def download_link(self, file_id: int) -> str:
        """Request a temporary download URL for a subtitle file.

        This consumes one unit of the account's daily download quota, so it's
        called at execute time rather than while building the plan.
        """
        self._login()
        data = self._request("/download", payload={"file_id": file_id})
        link = data.get("link")
        if not link:
            raise RuntimeError(f"OpenSubtitles returned no download link for file {file_id}")
        return link
