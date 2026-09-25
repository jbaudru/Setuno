"""Best-effort metadata enrichment for newly discovered tracks."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mutagen.id3 import (
    APIC,
    ID3,
    ID3NoHeaderError,
    TALB,
    TCON,
    TIT2,
    TPE1,
)
from mutagen.mp3 import MP3

from .database import default_data_dir


USER_AGENT = "Setuno/1.0 (local music library manager)"
REQUEST_TIMEOUT_SECONDS = 3

MIN_FUZZY_RATIO = 0.72
MAX_COVER_BYTES = 5 * 1024 * 1024

FIELDS = (
    "artist",
    "album",
    "genre",
    "cover_path",
)


# ===========================================================================
# Filename parsing
# ===========================================================================

FILENAME_SEPARATOR_PATTERN = re.compile(
    r"""
    \s*
    (?:
        -{1,3}
        |
        [–—]
    )
    \s*
    """,
    re.VERBOSE,
)

FEAT_PATTERN = re.compile(
    r"""
    \s*
    (?:
        feat(?:uring)?\.?
        |
        ft\.?
    )
    \s*
    """,
    re.IGNORECASE | re.VERBOSE,
)

COLLAB_SPLIT_PATTERN = re.compile(
    r"\s*(?:,|&|\bx\b|\bvs\.?\b)\s*",
    re.IGNORECASE,
)

LEADING_BRACKET_PATTERN = re.compile(
    r"""
    ^
    \s*
    \[
        ([^\]]+)
    \]
    \s*
    """,
    re.VERBOSE,
)

LEADING_PAREN_PATTERN = re.compile(
    r"""
    ^
    \s*
    \(
        ([^)]+)
    \)
    \s*
    """,
    re.VERBOSE,
)

LEADING_NOISE_PATTERN = re.compile(
    r"""
    ^
    \s*
    (?:
        youtube\s+music
        |
        youtube
        |
        soundcloud
        |
        spotify
        |
        official\s+audio
        |
        official\s+video
        |
        official
        |
        music\s+video
        |
        lyric\s+video
        |
        lyrics?
        |
        audio
        |
        video
        |
        visualizer
        |
        hq
        |
        hd
        |
        4k
        |
        clean
        |
        explicit
        |
        promo
        |
        preview
        |
        download
        |
        free\s+download
        |
        full\s+song
        |
        original
        |
        rip
        |
        web
        |
        cd
        |
        vinyl
        |
        single
        |
        album
        |
        track
        |
        disc
    )
    \s*
    (?:[-_:|]+\s*|\s+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

TRACK_NUMBER_PATTERN = re.compile(
    r"""
    ^
    \s*
    (?:
        \d{1,3}
        |
        \d{1,3}\.\d{1,3}
    )
    \s*
    (?:[._-]\s*|\s{2,})
    """,
    re.VERBOSE,
)

TRAILING_TRACK_NUMBER_PATTERN = re.compile(
    r"""
    ^
    (?:
        \d{1,3}
        |
        \d{1,3}\.\d{1,3}
    )
    \s+
    """,
    re.VERBOSE,
)

DOWNLOAD_SUFFIX_PATTERN = re.compile(
    r"""
    \s*
    (?:
        \(\s*www\.[^)]+\s*\)
        |
        \[\s*(?:www\.)?[^]]+\s*\]
        |
        -\s*(?:download|youtube|official)
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

PAREN_NOISE_PATTERN = re.compile(
    r"""
    \s*
    \(
        (?:
            official(?:\s+(?:video|audio|lyric\s+video|music\s+video))?
            |
            lyrics?
            |
            lyric\s+video
            |
            hq
            |
            hd
            |
            4k
            |
            mv
            |
            visualizer
            |
            free\s+download
            |
            download
            |
            album\s+version
            |
            explicit
            |
            clean
            |
            audio\s+only
            |
            full\s+song
            |
            with\s+lyrics
            |
            video
        )
    \)
    \s*
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ===========================================================================
# Generic text helpers
# ===========================================================================

def _clean_text(text: str) -> str:
    """Normalize whitespace and common filename artefacts."""
    if not text:
        return ""

    text = str(text)

    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\u200c", "")
    text = text.replace("\u200d", "")
    text = text.replace("\ufeff", "")

    text = re.sub(r"\s+", " ", text)

    return text.strip(" .-_")


def _strip_noise(text: str) -> str:
    """
    Remove obvious filename/download noise.

    Bracketed prefixes are deliberately preserved here because they can
    contain a label/source which may be useful as an album fallback.
    """
    if not text:
        return ""

    text = DOWNLOAD_SUFFIX_PATTERN.sub(
        "",
        text,
    )

    text = PAREN_NOISE_PATTERN.sub(
        " ",
        text,
    )

    text = TRACK_NUMBER_PATTERN.sub(
        "",
        text,
    )

    return _clean_text(text)


def _is_technical_label(value: str) -> bool:
    """Return True for bracketed values that are clearly not labels."""
    normalized = _normalize_for_match(value)

    return normalized in {
        "youtube",
        "youtube music",
        "soundcloud",
        "spotify",
        "official",
        "official audio",
        "official video",
        "audio",
        "video",
        "music video",
        "lyric video",
        "lyrics",
        "visualizer",
        "hq",
        "hd",
        "4k",
        "clean",
        "explicit",
        "promo",
        "preview",
        "download",
        "free download",
        "full song",
        "original",
        "rip",
        "web",
        "cd",
        "vinyl",
        "single",
        "album",
        "track",
        "disc",
    }


def _extract_leading_label(
    text: str,
) -> tuple[str, str]:
    """
    Remove leading metadata blocks and return:

        (remaining_text, possible_label)

    Examples:

        [UKF] AC Slater & MPH
            -> ("AC Slater & MPH", "UKF")

        [YouTube] [UKF] AC Slater
            -> ("AC Slater", "UKF")

        [Official Audio] Artist
            -> ("Artist", "")
    """
    if not text:
        return "", ""

    text = _clean_text(text)

    label = ""
    previous = None

    while text != previous:
        previous = text

        text = TRACK_NUMBER_PATTERN.sub(
            "",
            text,
        ).strip()

        bracket_match = LEADING_BRACKET_PATTERN.match(
            text
        )

        if bracket_match:
            candidate = _clean_text(
                bracket_match.group(1)
            )

            text = text[
                bracket_match.end():
            ].strip()

            if (
                candidate
                and not _is_technical_label(candidate)
                and not label
            ):
                label = candidate

            continue

        paren_match = LEADING_PAREN_PATTERN.match(
            text
        )

        if paren_match:
            candidate = _clean_text(
                paren_match.group(1)
            )

            text = text[
                paren_match.end():
            ].strip()

            if (
                candidate
                and not _is_technical_label(candidate)
                and not label
            ):
                label = candidate

            continue

        noise_match = LEADING_NOISE_PATTERN.match(
            text
        )

        if noise_match:
            text = text[
                noise_match.end():
            ].strip()

            continue

        break

    return (
        _clean_text(text),
        _clean_text(label),
    )


def _clean_artist_prefix(
    text: str,
) -> tuple[str, str]:
    """Return (artist_text, possible_label)."""
    return _extract_leading_label(text)


# ===========================================================================
# Artist parsing
# ===========================================================================

def _split_featuring(
    artist_field: str,
) -> tuple[str, str]:
    """Return (main artist, featured artist)."""
    artist_field = _clean_text(
        artist_field
    )

    match = FEAT_PATTERN.search(
        artist_field
    )

    if not match:
        return artist_field, ""

    main = artist_field[
        :match.start()
    ].strip()

    featured = artist_field[
        match.end():
    ].strip()

    return main, featured


def _normalize_artist(
    artist_field: str,
) -> tuple[str, str]:
    """Return (artist, featured artist)."""
    artist_field = _clean_text(
        artist_field
    )

    main, featured = _split_featuring(
        artist_field
    )

    parts = [
        p.strip()
        for p in COLLAB_SPLIT_PATTERN.split(
            main
        )
        if p.strip()
    ]

    artist = ", ".join(parts)

    return (
        _clean_artist_value(artist),
        _clean_text(featured),
    )


# ===========================================================================
# Filename structure detection
# ===========================================================================

def _looks_like_track_number(
    value: str,
) -> bool:
    """
    Detect track-number prefixes such as:

        01 Atlas
        1 Atlas
        01. Atlas
        1.02 Atlas
    """
    if not value:
        return False

    return bool(
        TRAILING_TRACK_NUMBER_PATTERN.match(
            value.strip()
        )
    )


def _remove_track_number(
    value: str,
) -> str:
    """Remove a leading track number from a title."""
    if not value:
        return ""

    value = value.strip()

    value = re.sub(
        r"""
        ^
        \s*
        (?:
            \d{1,3}
            |
            \d{1,3}\.\d{1,3}
        )
        \s*
        (?:[-._]\s*|\s+)
        """,
        "",
        value,
        flags=re.VERBOSE,
    )

    return _clean_title_value(
        value
    )


def _split_filename_parts(
    stem: str,
) -> list[str]:
    """Split a filename on Artist - Album - Title style separators."""
    return [
        part.strip()
        for part in FILENAME_SEPARATOR_PATTERN.split(
            stem
        )
        if part.strip()
    ]


def _parse_filename(
    path: Path,
) -> dict:
    """
    Parse common music filename conventions.

    Supported examples:

        Artist - Title
        [Label] Artist - Title
        Artist - Album - Title
        Artist - Album - 01 Title
        [Label] Artist - Album - 01 Title
        Artist feat. Guest - Title
        Artist & Artist - Title
        Artist x Artist - Title
        Artist, Artist - Title
        Artist_Track
    """
    stem = _strip_noise(
        path.stem
    )

    if not stem:
        return {
            "artist": "",
            "album": "",
            "title": "",
        }

    # ---------------------------------------------------------------
    # Extract [LABEL] before splitting.
    #
    # This prevents:
    #
    #     [UKF] AC Slater & MPH - Lights On
    #
    # from ever considering UKF to be the artist.
    # ---------------------------------------------------------------

    cleaned_stem, filename_label = (
        _extract_leading_label(stem)
    )

    parts = _split_filename_parts(
        cleaned_stem
    )

    # ---------------------------------------------------------------
    # Artist - Album - Title
    #
    # The three-part form is especially useful for:
    #
    #     Bicep - Isles - 01 Atlas
    #
    # We recognize the third part as a track title when it starts
    # with a track number.
    # ---------------------------------------------------------------

    if len(parts) >= 3:
        artist_raw = parts[0]

        middle = parts[1]

        title_raw = FILENAME_SEPARATOR_PATTERN.split(
            cleaned_stem,
            maxsplit=2,
        )[-1].strip()

        # More than three parts can occur with filenames such as:
        #
        # Artist - Album - Disc 1 - 01 Track
        #
        # In that case everything between artist and title is treated
        # as album information.
        if len(parts) == 3:
            album_raw = middle
            title_raw = parts[2]

        else:
            # Find the first and last separator positions.
            separator_matches = list(
                FILENAME_SEPARATOR_PATTERN.finditer(
                    cleaned_stem
                )
            )

            if len(separator_matches) >= 2:
                first_end = (
                    separator_matches[0].end()
                )

                last_start = (
                    separator_matches[-1].start()
                )

                album_raw = cleaned_stem[
                    first_end:last_start
                ].strip()

                title_raw = cleaned_stem[
                    separator_matches[-1].end():
                ].strip()
            else:
                album_raw = middle

        # A three-part filename is particularly convincing when the
        # final component contains a track number:
        #
        # Bicep - Isles - 01 Atlas
        #
        # If there is no track number, we still accept the structure
        # because Artist - Album - Title is a common convention.
        title = _remove_track_number(
            title_raw
        )

        artist_clean, embedded_label = (
            _clean_artist_prefix(
                artist_raw
            )
        )

        artist, featured = (
            _normalize_artist(
                artist_clean
            )
        )

        if featured:
            title = (
                f"{title} "
                f"(feat. {featured})"
            )

        album = _clean_text(
            album_raw
        )

        # A detected filename label is a better fallback than nothing,
        # but the explicit Artist - Album - Title album takes priority.
        if not album:
            album = filename_label

        return {
            "artist": artist,
            "album": album,
            "title": title,
        }

    # ---------------------------------------------------------------
    # Artist - Title
    # ---------------------------------------------------------------

    if len(parts) == 2:
        artist_raw = parts[0]
        title_raw = parts[1]

        artist_clean, embedded_label = (
            _clean_artist_prefix(
                artist_raw
            )
        )

        artist, featured = (
            _normalize_artist(
                artist_clean
            )
        )

        title = _remove_track_number(
            title_raw
        )

        if featured:
            title = (
                f"{title} "
                f"(feat. {featured})"
            )

        return {
            "artist": artist,
            "album": filename_label or embedded_label,
            "title": title,
        }

    # ---------------------------------------------------------------
    # Artist_Title
    # ---------------------------------------------------------------

    underscore_parts = [
        part.strip()
        for part in cleaned_stem.split("_")
        if part.strip()
    ]

    if len(underscore_parts) == 2:
        artist_raw = underscore_parts[0]

        artist_clean, embedded_label = (
            _clean_artist_prefix(
                artist_raw
            )
        )

        artist, featured = (
            _normalize_artist(
                artist_clean
            )
        )

        title = _remove_track_number(
            underscore_parts[1]
        )

        if featured:
            title = (
                f"{title} "
                f"(feat. {featured})"
            )

        return {
            "artist": artist,
            "album": filename_label or embedded_label,
            "title": title,
        }

    # ---------------------------------------------------------------
    # No artist/title separator.
    #
    # Treat the remaining filename as title.
    # ---------------------------------------------------------------

    return {
        "artist": "",
        "album": filename_label,
        "title": _remove_track_number(
            cleaned_stem
        ),
    }


# ===========================================================================
# Metadata cleanup
# ===========================================================================

def _clean_artist_value(
    value: str,
) -> str:
    """Normalize an artist field."""
    if not value:
        return ""

    value = _clean_text(value)

    value = re.sub(
        r"\s*[\(\[]\d+[\)\]]\s*$",
        "",
        value,
    )

    value = re.sub(
        r"""
        \s*
        \[
            \s*
            (?:feat\.?|ft\.?|featuring)
            \s+
            ([^\]]+)
        \]
        """,
        r" (feat. \1)",
        value,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    value = re.sub(
        r"\s+(?:ft\.?|featuring)\s+",
        " feat. ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\s*;\s*",
        ", ",
        value,
    )

    value = re.sub(
        r",\s*,+",
        ", ",
        value,
    )

    return _clean_text(value)


def _clean_title_value(
    value: str,
) -> str:
    """Normalize a title without destroying remix/version information."""
    if not value:
        return ""

    value = _clean_text(value)

    value = re.sub(
        r"\s*[\(\[]\d+[\)\]]\s*$",
        "",
        value,
    )

    return _clean_text(value)


def _clean_metadata(
    metadata: dict,
) -> dict:
    """Clean metadata without inventing information."""
    if not metadata:
        return metadata

    if metadata.get("artist"):
        metadata["artist"] = (
            _clean_artist_value(
                metadata["artist"]
            )
        )

    if metadata.get("title"):
        metadata["title"] = (
            _clean_title_value(
                metadata["title"]
            )
        )

    if metadata.get("album"):
        metadata["album"] = _clean_text(
            metadata["album"]
        )

    if metadata.get("genre"):
        metadata["genre"] = _clean_text(
            metadata["genre"]
        )

    return metadata


# ===========================================================================
# Matching
# ===========================================================================

def _normalize_for_match(
    value: str,
) -> str:
    """Aggressive normalization used only for comparisons."""
    if not value:
        return ""

    value = value.lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = re.sub(
        r"[^\w\s]",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def _similar(
    a: str,
    b: str,
) -> float:
    a = _normalize_for_match(a)
    b = _normalize_for_match(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    return SequenceMatcher(
        None,
        a,
        b,
    ).ratio()


def _artist_similarity(
    a: str,
    b: str,
) -> float:
    """Compare artists while tolerating collaboration formatting."""
    if not a or not b:
        return 0.0

    def split_artists(
        value: str,
    ) -> set[str]:
        return {
            _normalize_for_match(part)
            for part in COLLAB_SPLIT_PATTERN.split(
                value
            )
            if part.strip()
        }

    a_parts = split_artists(a)
    b_parts = split_artists(b)

    if not a_parts or not b_parts:
        return _similar(a, b)

    if a_parts == b_parts:
        return 1.0

    intersection = a_parts & b_parts

    if intersection:
        return max(
            0.8,
            len(intersection)
            / max(
                len(a_parts),
                len(b_parts),
            ),
        )

    return _similar(a, b)


def _match_score(
    title: str,
    artist: str,
    candidate_title: str,
    candidate_artist: str,
) -> float:
    """Return a combined 0..1 title/artist score."""
    title_score = _similar(
        title,
        candidate_title,
    )

    artist_score = (
        _artist_similarity(
            artist,
            candidate_artist,
        )
        if artist and candidate_artist
        else 0.5
    )

    return (
        0.65 * title_score
        + 0.35 * artist_score
    )


def _missing_fields(
    metadata: dict,
) -> set:
    return {
        field
        for field in FIELDS
        if not metadata.get(field)
    }


# ===========================================================================
# HTTP
# ===========================================================================

def _get_json(
    url: str,
) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(
        request,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def _download_cover(
    cache_key: str,
    image_url: str,
) -> str:
    """Download and cache cover artwork."""
    if not cache_key or not image_url:
        return ""

    try:
        cover_dir = (
            default_data_dir()
            / "covers"
        )

        cover_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        filename_base = hashlib.sha256(
            cache_key.encode("utf-8")
        ).hexdigest()

        jpg_path = (
            cover_dir
            / f"{filename_base}.jpg"
        )

        png_path = (
            cover_dir
            / f"{filename_base}.png"
        )

        if (
            jpg_path.exists()
            and jpg_path.stat().st_size
        ):
            return str(jpg_path)

        if (
            png_path.exists()
            and png_path.stat().st_size
        ):
            return str(png_path)

        request = Request(
            image_url,
            headers={
                "User-Agent": USER_AGENT,
            },
        )

        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            image = response.read(
                MAX_COVER_BYTES + 1
            )

            content_type = (
                response.headers.get_content_type()
            )

        if len(image) > MAX_COVER_BYTES:
            return ""

        if content_type == "image/png":
            extension = ".png"

        elif content_type in {
            "image/jpeg",
            "image/jpg",
        }:
            extension = ".jpg"

        elif image.startswith(b"\x89PNG"):
            extension = ".png"

        elif image.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"

        else:
            return ""

        path = (
            cover_dir
            / f"{filename_base}{extension}"
        )

        path.write_bytes(image)

        return str(path)

    except Exception:
        return ""


# ===========================================================================
# MusicBrainz
# ===========================================================================

def _lookup_musicbrainz(
    title: str,
    artist: str,
) -> dict:
    """Search MusicBrainz and select the best matching recording."""
    clean_title = _clean_title_value(
        title
    )

    clean_artist = _clean_artist_value(
        artist
    )

    queries = []

    if clean_artist:
        queries.append(
            f'recording:"{clean_title}" '
            f'AND artist:"{clean_artist}"'
        )

    queries.append(
        f'recording:"{clean_title}"'
    )

    for query in queries:
        try:
            url = (
                "https://musicbrainz.org/ws/2/recording/?"
                + urlencode(
                    {
                        "query": query,
                        "fmt": "json",
                        "limit": 10,
                        "inc": (
                            "artists+releases+genres"
                        ),
                    }
                )
            )

            data = _get_json(url)

            recordings = data.get(
                "recordings",
                [],
            )

            if not recordings:
                continue

            best = None
            best_score = 0.0

            for recording in recordings:
                artist_credit = (
                    recording.get(
                        "artist-credit"
                    )
                    or []
                )

                candidate_artist = ", ".join(
                    credit.get(
                        "name",
                        "",
                    ).strip()
                    for credit in artist_credit
                    if credit.get("name")
                ).strip()

                candidate_artist = (
                    _clean_artist_value(
                        candidate_artist
                    )
                )

                candidate_title = (
                    recording.get("title")
                    or recording.get("name")
                    or ""
                )

                score = _match_score(
                    clean_title,
                    clean_artist,
                    candidate_title,
                    candidate_artist,
                )

                mb_score = float(
                    recording.get("score") or 0
                ) / 100.0

                combined = (
                    0.7 * score
                    + 0.3 * mb_score
                )

                if combined > best_score:
                    best_score = combined
                    best = recording

            if not best:
                continue

            artist_credit = (
                best.get("artist-credit")
                or []
            )

            candidate_artist = ", ".join(
                credit.get(
                    "name",
                    "",
                ).strip()
                for credit in artist_credit
                if credit.get("name")
            ).strip()

            candidate_artist = (
                _clean_artist_value(
                    candidate_artist
                )
            )

            candidate_title = (
                best.get("title")
                or best.get("name")
                or ""
            )

            if _match_score(
                clean_title,
                clean_artist,
                candidate_title,
                candidate_artist,
            ) < 0.68:
                continue

            releases = (
                best.get("releases")
                or []
            )

            release = {}

            for candidate in releases:
                if candidate.get("id"):
                    release = candidate
                    break

            genres = (
                best.get("genres")
                or []
            )

            result = {
                "artist": candidate_artist,
                "album": _clean_text(
                    release.get(
                        "title",
                        "",
                    )
                ),
                "genre": _clean_text(
                    (
                        genres[0].get(
                            "name",
                            "",
                        )
                        if genres
                        else ""
                    )
                ),
            }

            if release.get("id"):
                result["cover_url"] = (
                    "https://coverartarchive.org/"
                    f"release/{release['id']}/front-500"
                )

                result["cover_key"] = (
                    release["id"]
                )

            return result

        except Exception:
            continue

    return {}


# ===========================================================================
# iTunes
# ===========================================================================

def _lookup_itunes(
    title: str,
    artist: str,
) -> dict:
    """Search iTunes and select the best matching track."""
    clean_title = _clean_title_value(
        title
    )

    clean_artist = _clean_artist_value(
        artist
    )

    term = (
        f"{clean_artist} {clean_title}"
        .strip()
    )

    try:
        url = (
            "https://itunes.apple.com/search?"
            + urlencode(
                {
                    "term": term,
                    "media": "music",
                    "entity": "song",
                    "limit": 10,
                }
            )
        )

        results = _get_json(url).get(
            "results",
            [],
        )

        if not results:
            return {}

        best = None
        best_score = 0.0

        for track in results:
            candidate_title = track.get(
                "trackName",
                "",
            )

            candidate_artist = track.get(
                "artistName",
                "",
            )

            score = _match_score(
                clean_title,
                clean_artist,
                candidate_title,
                candidate_artist,
            )

            if score > best_score:
                best_score = score
                best = track

        if (
            not best
            or best_score < MIN_FUZZY_RATIO
        ):
            return {}

        artwork = best.get(
            "artworkUrl100",
            "",
        )

        result = {
            "artist": _clean_artist_value(
                best.get(
                    "artistName",
                    "",
                )
            ),
            "album": _clean_text(
                best.get(
                    "collectionName",
                    "",
                )
            ),
            "genre": _clean_text(
                best.get(
                    "primaryGenreName",
                    "",
                )
            ),
        }

        if artwork:
            artwork = artwork.replace(
                "100x100bb",
                "600x600bb",
            )

            result["cover_url"] = artwork

            result["cover_key"] = (
                f"itunes:"
                f"{best.get('trackId', artwork)}"
            )

        return result

    except Exception:
        return {}


# ===========================================================================
# Deezer
# ===========================================================================

def _lookup_deezer(
    title: str,
    artist: str,
) -> dict:
    """Search Deezer and select the best matching track."""
    clean_title = _clean_title_value(
        title
    )

    clean_artist = _clean_artist_value(
        artist
    )

    query = (
        f'track:"{clean_title}"'
        + (
            f' artist:"{clean_artist}"'
            if clean_artist
            else ""
        )
    )

    try:
        url = (
            "https://api.deezer.com/search?"
            + urlencode(
                {
                    "q": query,
                    "limit": 10,
                }
            )
        )

        results = _get_json(url).get(
            "data",
            [],
        )

        if not results:
            return {}

        best = None
        best_score = 0.0

        for track in results:
            candidate_title = track.get(
                "title",
                "",
            )

            candidate_artist = (
                track.get("artist")
                or {}
            ).get(
                "name",
                "",
            )

            score = _match_score(
                clean_title,
                clean_artist,
                candidate_title,
                candidate_artist,
            )

            if score > best_score:
                best_score = score
                best = track

        if (
            not best
            or best_score < MIN_FUZZY_RATIO
        ):
            return {}

        album = (
            best.get("album")
            or {}
        )

        result = {
            "artist": _clean_artist_value(
                (
                    best.get("artist")
                    or {}
                ).get(
                    "name",
                    "",
                )
            ),
            "album": _clean_text(
                album.get(
                    "title",
                    "",
                )
            ),
            "genre": "",
        }

        cover = (
            album.get("cover_xl")
            or album.get("cover_big")
            or album.get("cover_medium")
        )

        if cover:
            result["cover_url"] = cover

            result["cover_key"] = (
                f"deezer:"
                f"{album.get('id', cover)}"
            )

        return result

    except Exception:
        return {}


SOURCES = (
    _lookup_musicbrainz,
    _lookup_itunes,
    _lookup_deezer,
)


# ===========================================================================
# Online enrichment
# ===========================================================================

def enrich_metadata(
    metadata: dict,
) -> dict:
    """
    Fill ONLY missing metadata fields using online sources.

    Existing metadata always has priority over online information.
    """
    if not metadata:
        return metadata

    metadata = _clean_metadata(
        metadata
    )

    title = _clean_title_value(
        metadata.get("title") or ""
    )

    if not title:
        return metadata

    artist = _clean_artist_value(
        metadata.get("artist") or ""
    )

    search_artist = (
        artist.split(",")[0].strip()
        if artist
        else ""
    )

    # Online enrichment is only useful when something is missing.
    if not _missing_fields(metadata):
        return metadata

    for lookup in SOURCES:
        if not _missing_fields(metadata):
            break

        try:
            found = lookup(
                title,
                search_artist,
            )

        except Exception:
            continue

        if not found:
            continue

        # IMPORTANT:
        #
        # Never replace existing local metadata.
        #
        # This means:
        #
        # ID3 artist > filename artist > online artist
        # ID3 album  > filename album  > online album
        # ID3 title  > filename title  > online title
        #
        # In practice, by the time we reach here the filename has already
        # filled whatever was missing from the original ID3 metadata.

        if not metadata.get("artist"):
            found_artist = (
                _clean_artist_value(
                    found.get("artist") or ""
                )
            )

            if found_artist:
                metadata["artist"] = (
                    found_artist
                )

        if not metadata.get("album"):
            album = _clean_text(
                found.get("album") or ""
            )

            if album:
                metadata["album"] = album

        if not metadata.get("genre"):
            genre = _clean_text(
                found.get("genre") or ""
            )

            if genre:
                metadata["genre"] = genre

        if (
            not metadata.get("cover_path")
            and found.get("cover_url")
        ):
            cover_path = _download_cover(
                found.get(
                    "cover_key",
                    found["cover_url"],
                ),
                found["cover_url"],
            )

            if cover_path:
                metadata["cover_path"] = (
                    cover_path
                )

    return _clean_metadata(
        metadata
    )


# ===========================================================================
# ID3
# ===========================================================================

def _read_existing_tags(
    path: Path,
) -> dict:
    """
    Read the original embedded metadata.

    This happens BEFORE filename parsing and BEFORE online lookup.
    """
    empty = {
        "artist": "",
        "title": "",
        "album": "",
        "genre": "",
        "cover_path": "",
    }

    try:
        audio = MP3(
            path,
            ID3=ID3,
        )

    except Exception:
        return empty

    tags = audio.tags

    if not tags:
        return empty

    def _tag_value(
        key: str,
    ) -> str:
        try:
            value = tags.get(key)

            if value is None:
                return ""

            if hasattr(
                value,
                "text",
            ):
                values = [
                    str(item).strip()
                    for item in value.text
                    if str(item).strip()
                ]

                return ", ".join(values)

            return str(value).strip()

        except Exception:
            return ""

    return {
        "artist": _clean_artist_value(
            _tag_value("TPE1")
        ),
        "title": _clean_title_value(
            _tag_value("TIT2")
        ),
        "album": _clean_text(
            _tag_value("TALB")
        ),
        "genre": _clean_text(
            _tag_value("TCON")
        ),
        "cover_path": "",
    }


def _write_tags(
    path: Path,
    metadata: dict,
) -> None:
    """Write managed ID3 fields while preserving unrelated frames."""
    metadata = _clean_metadata(
        dict(metadata)
    )

    try:
        tags = ID3(path)

    except ID3NoHeaderError:
        tags = ID3()

    except Exception:
        tags = ID3()

    field_frames = {
        "title": (
            "TIT2",
            TIT2,
        ),
        "artist": (
            "TPE1",
            TPE1,
        ),
        "album": (
            "TALB",
            TALB,
        ),
        "genre": (
            "TCON",
            TCON,
        ),
    }

    for field, (
        frame_id,
        frame_type,
    ) in field_frames.items():

        value = _clean_text(
            metadata.get(field) or ""
        )

        if not value:
            continue

        tags.setall(
            frame_id,
            [
                frame_type(
                    encoding=3,
                    text=value,
                )
            ],
        )

    cover_path = metadata.get(
        "cover_path"
    )

    if cover_path:
        cover = Path(cover_path)

        if (
            cover.exists()
            and cover.is_file()
        ):
            try:
                data = cover.read_bytes()

                if len(data) <= MAX_COVER_BYTES:
                    if (
                        cover.suffix.lower()
                        == ".png"
                    ):
                        mime = "image/png"
                    else:
                        mime = "image/jpeg"

                    tags.delall("APIC")

                    tags.add(
                        APIC(
                            encoding=3,
                            mime=mime,
                            type=3,
                            desc="Cover",
                            data=data,
                        )
                    )

            except Exception:
                pass

    try:
        tags.save(
            path,
            v2_version=3,
        )

    except Exception:
        pass


# ===========================================================================
# File-level enrichment
# ===========================================================================

def enrich_file(
    path: Path,
) -> dict:
    """
    Enrich a track with the following strict priority:

        1. Existing embedded ID3 metadata
        2. Filename metadata for missing fields
        3. Online metadata for still-missing fields

    Examples:

        [UKF] AC Slater & MPH - Lights On.mp3

            Existing ID3:
                artist = ""
                album  = ""
                title  = ""

            Result:
                artist = "AC Slater, MPH"
                album  = "UKF"
                title  = "Lights On"


        Bicep - Isles - 01 Atlas.mp3

            Result:
                artist = "Bicep"
                album  = "Isles"
                title  = "Atlas"


        Existing ID3:
            artist = "Correct Artist"
            album  = "Correct Album"
            title  = "Correct Title"

        Filename:
            [Wrong Label] Something Else - Something Else

        Result:
            Existing metadata remains untouched.

    Online lookup is only used after the local metadata and filename
    information have been considered.
    """

    path = Path(path)

    # ===============================================================
    # 1. ALWAYS READ ORIGINAL EMBEDDED METADATA FIRST
    # ===============================================================

    metadata = _read_existing_tags(
        path
    )

    if not metadata:
        metadata = {
            "artist": "",
            "title": "",
            "album": "",
            "genre": "",
            "cover_path": "",
        }

    original_metadata = dict(
        metadata
    )

    # ===============================================================
    # 2. USE FILENAME ONLY TO FILL MISSING FIELDS
    # ===============================================================

    guessed = _parse_filename(
        path
    )

    if (
        not metadata.get("artist")
        and guessed.get("artist")
    ):
        metadata["artist"] = (
            guessed["artist"]
        )

    if (
        not metadata.get("album")
        and guessed.get("album")
    ):
        metadata["album"] = (
            guessed["album"]
        )

    if (
        not metadata.get("title")
        and guessed.get("title")
    ):
        metadata["title"] = (
            guessed["title"]
        )

    metadata = _clean_metadata(
        metadata
    )

    # ===============================================================
    # 3. ONLINE ENRICHMENT
    #
    # At this point the lookup has as much local information as
    # possible, but it still cannot overwrite any existing value.
    # ===============================================================

    metadata = enrich_metadata(
        metadata
    )

    metadata = _clean_metadata(
        metadata
    )

    # ===============================================================
    # 4. WRITE ONLY WHEN SOMETHING CHANGED
    # ===============================================================

    if metadata != original_metadata:
        _write_tags(
            path,
            metadata,
        )

    return metadata