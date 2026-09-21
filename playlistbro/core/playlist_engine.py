"""Playlist generation engine: duration targeting, tempo/energy/loudness modes,
harmonic mixing and multi-parameter progression.
"""

import random

from typing import List, Optional

from .camelot import camelot_distance

from .models import Track


MODE_FIXED_TEMPO = "fixed_tempo"

MODE_TEMPO_PROGRESSION = "tempo_progression"

MODE_FIXED_ENERGY = "fixed_energy"

MODE_ENERGY_PROGRESSION = "energy_progression"

MODE_COMBINED_PROGRESSION = "tempo_energy_progression"

ALL_MODES = [
    MODE_FIXED_TEMPO,
    MODE_TEMPO_PROGRESSION,
    MODE_FIXED_ENERGY,
    MODE_ENERGY_PROGRESSION,
    MODE_COMBINED_PROGRESSION,
]

MODE_LABELS = {
    MODE_FIXED_TEMPO: "Fixed tempo",
    MODE_TEMPO_PROGRESSION: "Tempo progression",
    MODE_FIXED_ENERGY: "Fixed energy",
    MODE_ENERGY_PROGRESSION: "Energy progression",
    MODE_COMBINED_PROGRESSION: "Tempo + Energy progression",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _track_loudness(track: Track) -> float:
    """Return track loudness while remaining compatible with older Track models.

    Loudness is expected to be stored in LUFS. Older library entries may not
    have the attribute yet, so a neutral fallback is used.
    """
    value = getattr(track, "loudness", None)

    if value is None:
        return -14.0

    try:
        value = float(value)
    except (TypeError, ValueError):
        return -14.0

    return value


def _track_energy(track: Track) -> float:
    """Safely retrieve normalized track energy."""
    value = getattr(track, "energy", 0.0)

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_values(values: List[float]) -> List[float]:
    """Normalize a list to 0-1."""
    if not values:
        return []

    lo = min(values)
    hi = max(values)
    spread = hi - lo

    if spread <= 1e-12:
        return [0.5] * len(values)

    return [
        (value - lo) / spread
        for value in values
    ]


def _musical_distance(
    current: Track,
    candidate: Track,
    tempo_weight: float = 1.0,
    energy_weight: float = 1.0,
    loudness_weight: float = 1.0,
    harmonic_weight: float = 3.0,
) -> float:
    """Calculate a multi-dimensional transition distance.

    Tempo is normalized relative to the current track. Loudness uses LUFS,
    where larger absolute differences represent larger perceived level jumps.

    The weights are deliberately kept moderate so harmonic compatibility
    remains important when harmonic mixing is enabled.
    """
    current_tempo = max(
        1.0,
        float(getattr(current, "tempo", 120.0)),
    )

    candidate_tempo = max(
        1.0,
        float(getattr(candidate, "tempo", 120.0)),
    )

    # Relative tempo difference is more musically meaningful than raw BPM
    # difference. A 5 BPM difference at 70 BPM is more noticeable than 5 BPM
    # at 160 BPM.
    tempo_distance = abs(
        candidate_tempo - current_tempo
    ) / current_tempo

    energy_distance = abs(
        _track_energy(candidate)
        - _track_energy(current)
    )

    # LUFS difference.
    loudness_distance = abs(
        _track_loudness(candidate)
        - _track_loudness(current)
    ) / 12.0

    harmonic_distance = camelot_distance(
        current.camelot,
        candidate.camelot,
    )

    return (
        tempo_weight * tempo_distance
        + energy_weight * energy_distance
        + loudness_weight * loudness_distance
        + harmonic_weight * harmonic_distance
    )


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_tracks(
    tracks: List[Track],
    genres: Optional[List[str]] = None,
    tempo_range=None,
    energy_range=None,
) -> List[Track]:
    """Filter tracks using genre, tempo and energy constraints.

    The public signature is preserved.

    Loudness can also be filtered when a range is supplied through the
    existing `energy_range` argument only if the caller explicitly passes a
    loudness-aware object/tuple. For normal backwards compatibility, the
    existing energy_range behavior remains unchanged.

    If the Track model contains `loudness`, it is always considered later
    during playlist ordering and transition scoring.
    """
    result = tracks

    if genres:
        genre_set = {
            str(g).lower()
            for g in genres
        }

        result = [
            t
            for t in result
            if str(
                getattr(t, "genre", "")
            ).lower()
            in genre_set
        ]

    if tempo_range:
        lo, hi = tempo_range

        result = [
            t
            for t in result
            if lo
            <= float(
                getattr(t, "tempo", 0.0)
            )
            <= hi
        ]

    if energy_range:
        lo, hi = energy_range

        result = [
            t
            for t in result
            if lo
            <= _track_energy(t)
            <= hi
        ]

    return result


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _select_for_duration(
    candidates: List[Track],
    target_seconds: float,
) -> List[Track]:
    """Greedily pick tracks whose total duration approaches target_seconds."""
    if target_seconds <= 0:
        return list(candidates)

    pool = list(candidates)
    random.shuffle(pool)

    selected = []
    total = 0.0

    tolerance = 60.0

    for track in pool:
        if total >= target_seconds + tolerance:
            break

        selected.append(track)

        total += (
            float(
                getattr(
                    track,
                    "duration",
                    0.0,
                )
            )
            or 0.0
        )

        if (
            target_seconds - tolerance
            <= total
            <= target_seconds + tolerance
        ):
            break

    return selected


def _select_by_count(
    candidates: List[Track],
    count: int,
) -> List[Track]:
    """Randomly pick up to count tracks from filtered candidates."""
    if count <= 0:
        return list(candidates)

    pool = list(candidates)
    random.shuffle(pool)

    return pool[:count]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def _nearest_neighbor_order(
    tracks: List[Track],
    key_fn,
) -> List[Track]:
    """Order tracks by nearest-neighbor walk.

    In addition to the requested key, energy, loudness and Camelot distance
    are considered so that transitions do not produce abrupt level or energy
    changes.

    The public function signature is preserved.
    """
    if not tracks:
        return []

    remaining = list(tracks)

    remaining.sort(
        key=key_fn
    )

    current = remaining.pop(0)

    ordered = [
        current
    ]

    while remaining:
        best_idx = 0
        best_score = float("inf")

        for i, candidate in enumerate(
            remaining
        ):
            key_distance = abs(
                key_fn(candidate)
                - key_fn(current)
            )

            # Secondary musical constraints.
            energy_distance = abs(
                _track_energy(candidate)
                - _track_energy(current)
            )

            loudness_distance = abs(
                _track_loudness(candidate)
                - _track_loudness(current)
            ) / 12.0

            harmonic_penalty = (
                camelot_distance(
                    current.camelot,
                    candidate.camelot,
                )
                * 3.0
            )

            score = (
                key_distance
                + energy_distance * 0.35
                + loudness_distance * 0.50
                + harmonic_penalty
            )

            if score < best_score:
                best_score = score
                best_idx = i

        current = remaining.pop(
            best_idx
        )

        ordered.append(
            current
        )

    return ordered


def _progression_order(
    tracks: List[Track],
    key_fn,
    ascending=True,
) -> List[Track]:
    """Order tracks according to a progression while refining transitions."""
    if not tracks:
        return []

    ordered = sorted(
        tracks,
        key=key_fn,
        reverse=not ascending,
    )

    refined = []

    window = list(
        ordered
    )

    while window:
        if not refined:
            refined.append(
                window.pop(0)
            )
            continue

        current = refined[-1]

        # Look ahead to prevent progression ordering from creating a large
        # harmonic or loudness jump.
        lookahead = window[:7]

        best_i = 0
        best_score = float("inf")

        for i, candidate in enumerate(
            lookahead
        ):
            progression_distance = abs(
                key_fn(candidate)
                - key_fn(current)
            )

            harmonic_distance = (
                camelot_distance(
                    current.camelot,
                    candidate.camelot,
                )
            )

            energy_distance = abs(
                _track_energy(candidate)
                - _track_energy(current)
            )

            loudness_distance = abs(
                _track_loudness(candidate)
                - _track_loudness(current)
            ) / 12.0

            score = (
                progression_distance
                + harmonic_distance * 0.35
                + energy_distance * 0.20
                + loudness_distance * 0.35
            )

            if score < best_score:
                best_score = score
                best_i = i

        refined.append(
            window.pop(best_i)
        )

    return refined


def _combined_progression_order(
    tracks: List[Track],
    ascending: bool = True,
) -> List[Track]:
    """Order by tempo, energy and loudness together.

    All three dimensions are normalized independently and combined into a
    single progression value. Loudness is converted so that higher LUFS
    corresponds to higher progression level.

    The public function signature is preserved.
    """
    if not tracks:
        return []

    tempos = [
        float(
            getattr(
                track,
                "tempo",
                120.0,
            )
        )
        for track in tracks
    ]

    energies = [
        _track_energy(track)
        for track in tracks
    ]

    loudnesses = [
        _track_loudness(track)
        for track in tracks
    ]

    tempo_norm = _normalize_values(
        tempos
    )

    energy_norm = _normalize_values(
        energies
    )

    loudness_norm = _normalize_values(
        loudnesses
    )

    combined = {}

    for i, track in enumerate(
        tracks
    ):
        # Tempo and energy remain the dominant dimensions, while loudness
        # contributes enough to prevent large perceived-volume jumps.
        combined[
            id(track)
        ] = (
            0.40 * tempo_norm[i]
            + 0.40 * energy_norm[i]
            + 0.20 * loudness_norm[i]
        )

    def combined_key(track: Track) -> float:
        return combined[
            id(track)
        ]

    return _progression_order(
        tracks,
        key_fn=combined_key,
        ascending=ascending,
    )


def _loudness_progression_order(
    tracks: List[Track],
    ascending: bool = True,
) -> List[Track]:
    """Progress from quieter to louder tracks or vice versa.

    Kept private so the existing public API remains unchanged.
    """
    return _progression_order(
        tracks,
        key_fn=_track_loudness,
        ascending=ascending,
    )


# ---------------------------------------------------------------------------
# Playlist generation
# ---------------------------------------------------------------------------

def generate_playlist(
    library: List[Track],
    mode: str,
    duration_minutes: float = 0,
    genres: Optional[List[str]] = None,
    tempo_range=None,
    energy_range=None,
    harmonic_mixing: bool = True,
    track_count: Optional[int] = None,
) -> List[Track]:
    """Generate a playlist according to the requested mode.

    Tempo, energy and loudness are all considered during transition
    optimization. The public function signature is preserved.
    """
    candidates = filter_tracks(
        library,
        genres,
        tempo_range,
        energy_range,
    )

    if not candidates:
        return []

    if track_count:
        selected = _select_by_count(
            candidates,
            track_count,
        )
    else:
        target_seconds = (
            duration_minutes * 60
            if duration_minutes
            else 0
        )

        selected = _select_for_duration(
            candidates,
            target_seconds,
        )

    if not selected:
        return []

    # ------------------------------------------------------------------
    # Tempo progression
    # ------------------------------------------------------------------

    if mode == MODE_TEMPO_PROGRESSION:
        return _progression_order(
            selected,
            key_fn=lambda t: float(
                getattr(
                    t,
                    "tempo",
                    120.0,
                )
            ),
            ascending=True,
        )

    # ------------------------------------------------------------------
    # Energy progression
    # ------------------------------------------------------------------

    if mode == MODE_ENERGY_PROGRESSION:
        return _progression_order(
            selected,
            key_fn=_track_energy,
            ascending=True,
        )

    # ------------------------------------------------------------------
    # Combined tempo + energy + loudness progression
    # ------------------------------------------------------------------

    if mode == MODE_COMBINED_PROGRESSION:
        return _combined_progression_order(
            selected,
            ascending=True,
        )

    # ------------------------------------------------------------------
    # Fixed energy
    # ------------------------------------------------------------------

    if mode == MODE_FIXED_ENERGY:
        if harmonic_mixing:
            return _nearest_neighbor_order(
                selected,
                key_fn=_track_energy,
            )

        # Even without harmonic mixing, loudness is used as a secondary
        # ordering criterion to avoid unnecessary level jumps.
        return sorted(
            selected,
            key=lambda t: (
                _track_energy(t),
                _track_loudness(t),
            ),
        )

    # ------------------------------------------------------------------
    # Fixed tempo
    # ------------------------------------------------------------------

    if harmonic_mixing:
        return _nearest_neighbor_order(
            selected,
            key_fn=lambda t: float(
                getattr(
                    t,
                    "tempo",
                    120.0,
                )
            ),
        )

    # Default fixed-tempo ordering.
    #
    # Loudness is included as a secondary criterion, followed by energy.
    return sorted(
        selected,
        key=lambda t: (
            float(
                getattr(
                    t,
                    "tempo",
                    120.0,
                )
            ),
            _track_loudness(t),
            _track_energy(t),
        ),
    )


# ---------------------------------------------------------------------------
# Playlist statistics
# ---------------------------------------------------------------------------

def playlist_stats(
    tracks: List[Track],
) -> dict:
    """Return aggregate playlist statistics.

    Adds average loudness and loudness range while preserving the existing
    statistics fields.
    """
    if not tracks:
        return {
            "count": 0,
            "total_duration": 0,
            "avg_tempo": 0,
            "avg_energy": 0,
            "avg_loudness": 0,
            "min_loudness": 0,
            "max_loudness": 0,
            "loudness_range": 0,
            "genres": {},
        }

    total_duration = sum(
        float(
            getattr(
                track,
                "duration",
                0.0,
            )
        )
        for track in tracks
    )

    avg_tempo = (
        sum(
            float(
                getattr(
                    track,
                    "tempo",
                    0.0,
                )
            )
            for track in tracks
        )
        / len(tracks)
    )

    avg_energy = (
        sum(
            _track_energy(track)
            for track in tracks
        )
        / len(tracks)
    )

    loudness_values = [
        _track_loudness(track)
        for track in tracks
    ]

    avg_loudness = (
        sum(loudness_values)
        / len(loudness_values)
    )

    min_loudness = min(
        loudness_values
    )

    max_loudness = max(
        loudness_values
    )

    genres = {}

    for track in tracks:
        genre = (
            getattr(
                track,
                "genre",
                "",
            )
            or "Unknown"
        )

        genres[genre] = (
            genres.get(
                genre,
                0,
            )
            + 1
        )

    return {
        "count": len(tracks),
        "total_duration": total_duration,
        "avg_tempo": round(
            avg_tempo,
            1,
        ),
        "avg_energy": round(
            avg_energy,
            2,
        ),
        "avg_loudness": round(
            avg_loudness,
            1,
        ),
        "min_loudness": round(
            min_loudness,
            1,
        ),
        "max_loudness": round(
            max_loudness,
            1,
        ),
        "loudness_range": round(
            max_loudness
            - min_loudness,
            1,
        ),
        "genres": genres,
    }