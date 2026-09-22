"""Best-effort online metadata enrichment for newly discovered tracks."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .database import default_data_dir


USER_AGENT = "Setuno/1.0 (local music library manager)"
REQUEST_TIMEOUT_SECONDS = 2
MIN_MATCH_SCORE = 90
MAX_COVER_BYTES = 5 * 1024 * 1024


def _get_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_cover(release_id: str) -> str:
    try:
        request = Request(
            f"https://coverartarchive.org/release/{release_id}/front-250",
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            image = response.read(MAX_COVER_BYTES + 1)
            content_type = response.headers.get_content_type()
        if len(image) > MAX_COVER_BYTES or content_type not in {"image/jpeg", "image/png"}:
            return ""
        extension = ".png" if content_type == "image/png" else ".jpg"
        filename = hashlib.sha256(release_id.encode("utf-8")).hexdigest() + extension
        path = default_data_dir() / "covers" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        return str(path)
    except Exception:
        return ""


def enrich_metadata(metadata: dict) -> dict:
    """Fill empty fields and a missing cover using MusicBrainz when available.

    Network failures, bad responses, and uncertain matches leave local metadata unchanged.
    """
    if metadata.get("artist") and metadata.get("album") and metadata.get("genre") and metadata.get("cover_path"):
        return metadata

    title = (metadata.get("title") or "").strip()
    if not title:
        return metadata

    query = f'recording:"{title}"'
    artist = (metadata.get("artist") or "").strip()
    if artist:
        query += f' AND artist:"{artist}"'

    try:
        url = "https://musicbrainz.org/ws/2/recording/?" + urlencode({
            "query": query,
            "fmt": "json",
            "limit": 1,
            "inc": "artists+releases+genres",
        })
        recordings = _get_json(url).get("recordings", [])
        if not recordings or int(recordings[0].get("score", 0)) < MIN_MATCH_SCORE:
            return metadata
        recording = recordings[0]
        artist_credit = recording.get("artist-credit") or []
        releases = recording.get("releases") or []
        release = releases[0] if releases else {}
        metadata["title"] = metadata.get("title") or recording.get("title", "")
        metadata["artist"] = metadata.get("artist") or " ".join(
            credit.get("name", "") for credit in artist_credit
        ).strip()
        metadata["album"] = metadata.get("album") or release.get("title", "")
        genres = recording.get("genres") or []
        metadata["genre"] = metadata.get("genre") or (genres[0].get("name", "") if genres else "")
        if not metadata.get("cover_path") and release.get("id"):
            metadata["cover_path"] = _download_cover(release["id"])
    except Exception:
        pass
    return metadata