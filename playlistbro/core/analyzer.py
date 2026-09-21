"""Audio analysis: tempo, musical key, energy, loudness and genre extraction.

Deliberately dependency-light: decoding uses `miniaudio`, and all DSP
(STFT, onset, tempo, chroma, loudness and genre features) is implemented
with plain numpy so the packaged app stays small and portable.
"""

import hashlib
import os
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .camelot import normalize_key_name, key_to_camelot
from .database import default_data_dir


# Formats miniaudio can decode natively.
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg"}

NOTE_NAMES = [
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B",
]

# ---------------------------------------------------------------------------
# Musical key profiles
# ---------------------------------------------------------------------------

MAJOR_PROFILE = np.array([
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
    2.52, 5.19, 2.39, 3.66, 2.29, 2.88
], dtype=np.float64)

MINOR_PROFILE = np.array([
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
    2.54, 4.75, 3.98, 2.69, 3.34, 3.17
], dtype=np.float64)

# Secondary key profiles. Combining profiles helps avoid relying on one
# particular statistical key profile.
MAJOR_PROFILE_ALT = np.array([
    5.0, 2.0, 3.5, 2.0, 4.5, 4.0,
    2.0, 4.5, 2.0, 3.5, 2.0, 3.0
], dtype=np.float64)

MINOR_PROFILE_ALT = np.array([
    5.0, 2.5, 3.5, 4.5, 2.5, 3.5,
    2.5, 4.0, 4.0, 2.5, 3.5, 3.0
], dtype=np.float64)


# ---------------------------------------------------------------------------
# Analysis configuration
# ---------------------------------------------------------------------------

ANALYSIS_SR = 22050
ANALYSIS_MAX_SECONDS = 90

# General STFT.
FRAME_SIZE = 1024
HOP_SIZE = 128

# A second resolution is useful for tempo refinement.
TEMPO_FRAME_SIZE = 8192
TEMPO_HOP_SIZE = 2048

MIN_BPM, MAX_BPM = 80, 200

# Key analysis needs considerably finer frequency resolution.
KEY_FRAME_SIZE = 8192
KEY_HOP_SIZE = 2048
KEY_MIN_HZ, KEY_MAX_HZ = 55.0, 8000.0

# Waveform mini-preview.
WAVEFORM_POINTS = 60
WAVEFORM_LOW_HZ = 195.0
WAVEFORM_HIGH_HZ = 6000.0

# Loudness analysis.
LOUDNESS_MIN_DB = -60.0
LOUDNESS_MAX_DB = 0.0


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _read_tags(filepath: str) -> dict:
    tags = {
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
    }

    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(filepath, easy=True)

        if audio and audio.tags:
            tags["title"] = (
                audio.tags.get("title", [""])[0]
                if audio.tags.get("title")
                else ""
            )

            tags["artist"] = (
                audio.tags.get("artist", [""])[0]
                if audio.tags.get("artist")
                else ""
            )

            tags["album"] = (
                audio.tags.get("album", [""])[0]
                if audio.tags.get("album")
                else ""
            )

            genre = audio.tags.get("genre", [""])
            tags["genre"] = genre[0] if genre else ""

    except Exception:
        pass

    if not tags["title"]:
        tags["title"] = Path(filepath).stem

    return tags


def _extract_cover(filepath: str) -> str:
    """Cache embedded album art and return its path, or an empty string."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(filepath)

        if audio is None:
            return ""

        data, mime = None, "image/jpeg"

        tags = getattr(audio, "tags", None)

        if tags:
            for key in tags.keys():
                if str(key).startswith("APIC"):
                    apic = tags[key]
                    data = apic.data
                    mime = getattr(apic, "mime", mime) or mime
                    break

        if data is None and getattr(audio, "pictures", None):
            pic = audio.pictures[0]
            data = pic.data
            mime = pic.mime or mime

        if not data:
            return ""

        ext = ".png" if "png" in mime else ".jpg"

        digest = hashlib.md5(
            filepath.encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

        cover_dir = default_data_dir() / "covers"
        cover_dir.mkdir(parents=True, exist_ok=True)

        out_path = cover_dir / f"{digest}{ext}"

        if not out_path.exists():
            out_path.write_bytes(data)

        return str(out_path)

    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Audio decoding
# ---------------------------------------------------------------------------

def _decode_audio(filepath: str):
    """Decode filepath to mono float32 PCM at ANALYSIS_SR."""
    import miniaudio

    decoded = miniaudio.decode_file(
        filepath,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
        sample_rate=ANALYSIS_SR,
    )

    samples = np.frombuffer(
        bytes(decoded.samples),
        dtype=np.float32,
    )

    full_duration = len(samples) / float(ANALYSIS_SR)

    if full_duration > ANALYSIS_MAX_SECONDS:
        start = int(
            (full_duration - ANALYSIS_MAX_SECONDS)
            / 2
            * ANALYSIS_SR
        )

        end = start + int(
            ANALYSIS_MAX_SECONDS * ANALYSIS_SR
        )

        window = samples[start:end]
    else:
        window = samples

    return window, full_duration


# ---------------------------------------------------------------------------
# STFT / spectral analysis
# ---------------------------------------------------------------------------

def _stft_magnitude(
    y: np.ndarray,
    frame_size: int = FRAME_SIZE,
    hop_size: int = HOP_SIZE,
) -> np.ndarray:
    """Magnitude STFT using a Hann window and vectorized NumPy operations."""
    if len(y) < frame_size:
        y = np.pad(
            y,
            (0, frame_size - len(y)),
        )

    window = np.hanning(frame_size).astype(np.float32)

    frames = (
        sliding_window_view(y, frame_size)[::hop_size]
        * window
    )

    spectrum = np.fft.rfft(frames, axis=1)

    return np.abs(spectrum)


def _normalize_feature(x: np.ndarray) -> np.ndarray:
    """Robust feature normalization using median/MAD."""
    x = np.asarray(x, dtype=np.float64)

    if len(x) == 0:
        return x

    median = np.median(x)
    mad = np.median(np.abs(x - median))

    if mad < 1e-12:
        std = np.std(x)

        if std < 1e-12:
            return np.zeros_like(x)

        return (x - median) / std

    return (x - median) / (1.4826 * mad)


def _onset_envelope(mag: np.ndarray) -> np.ndarray:
    """Improved spectral-flux onset envelope.

    Uses log-compressed spectra and frequency weighting. This is more robust
    to strong bass content and large amplitude differences than raw spectral
    flux.
    """
    if mag.shape[0] < 2:
        return np.zeros(mag.shape[0], dtype=np.float64)

    # Log compression prevents loud kicks from dominating the whole envelope.
    log_mag = np.log1p(mag)

    diff = np.diff(log_mag, axis=0)
    positive_diff = np.maximum(diff, 0.0)

    n_bins = mag.shape[1]

    # Slightly emphasize the upper-mid region where many useful transients
    # occur, while retaining bass transients.
    freq_weight = np.linspace(
        0.75,
        1.25,
        n_bins,
        dtype=np.float64,
    )

    flux = (
        positive_diff
        * freq_weight[np.newaxis, :]
    ).sum(axis=1)

    flux = np.concatenate(
        [[0.0], flux]
    )

    # Robust normalization.
    flux = np.maximum(flux, 0.0)

    median = np.median(flux)

    if median > 1e-12:
        flux = flux / median

    # Remove slow variation while retaining rhythmic changes.
    smooth_len = 5

    if len(flux) >= smooth_len:
        kernel = np.ones(
            smooth_len,
            dtype=np.float64,
        ) / smooth_len

        smooth = np.convolve(
            flux,
            kernel,
            mode="same",
        )

        flux = np.maximum(
            flux - 0.35 * smooth,
            0.0,
        )

    return flux


# ---------------------------------------------------------------------------
# Tempo detection
# ---------------------------------------------------------------------------

def _interpolate_peak(values: np.ndarray, index: int) -> float:
    """Parabolic interpolation around a discrete peak."""
    if index <= 0 or index >= len(values) - 1:
        return float(index)

    y0 = float(values[index - 1])
    y1 = float(values[index])
    y2 = float(values[index + 1])

    denom = y0 - 2.0 * y1 + y2

    if abs(denom) < 1e-12:
        return float(index)

    delta = 0.5 * (y0 - y2) / denom
    delta = max(-1.0, min(1.0, delta))

    return float(index) + delta


def _tempo_candidates(
    onset_env: np.ndarray,
    sr: int,
    hop: int,
):
    """Return several plausible BPM candidates with confidence scores."""
    if len(onset_env) < 16:
        return [(120.0, 0.0)]

    x = np.asarray(
        onset_env,
        dtype=np.float64,
    )

    # Remove DC and normalize.
    x -= np.mean(x)

    std = np.std(x)

    if std < 1e-12:
        return [(120.0, 0.0)]

    x /= std

    # Mild smoothing improves beat-period stability.
    if len(x) >= 5:
        kernel = np.array(
            [1, 2, 3, 2, 1],
            dtype=np.float64,
        )
        kernel /= kernel.sum()

        x = np.convolve(
            x,
            kernel,
            mode="same",
        )

    # FFT autocorrelation is considerably faster than np.correlate for
    # longer analysis windows.
    n = len(x)
    fft_size = 1

    while fft_size < 2 * n:
        fft_size *= 2

    spectrum = np.fft.rfft(
        x,
        fft_size,
    )

    acf = np.fft.irfft(
        np.abs(spectrum) ** 2,
        fft_size,
    )[:n]

    if acf[0] <= 1e-12:
        return [(120.0, 0.0)]

    acf /= acf[0]

    min_lag = max(
        1,
        int(
            sr * 60.0
            / MAX_BPM
            / hop
        ),
    )

    max_lag = min(
        n - 1,
        int(
            sr * 60.0
            / MIN_BPM
            / hop
        ),
    )

    if max_lag <= min_lag:
        return [(120.0, 0.0)]

    lags = np.arange(
        min_lag,
        max_lag + 1,
    )

    base = acf[lags].copy()

    # Harmonic summation. We score both multiples and fractions of a beat
    # period to reduce 2x/0.5x tempo errors.
    scores = (
        1.00 * base
    )

    for harmonic, weight in (
        (2, 0.55),
        (3, 0.35),
        (4, 0.20),
    ):
        harmonic_lags = lags * harmonic

        valid = harmonic_lags < len(acf)

        scores[valid] += (
            weight
            * acf[harmonic_lags[valid]]
        )

    # Reward the 2x candidate when it has strong beat subdivisions, but not
    # so aggressively that every track gets interpreted at double tempo.
    for divisor, weight in (
        (2, 0.25),
        (3, 0.12),
    ):
        sub_lags = np.maximum(
            1,
            np.rint(lags / divisor).astype(int),
        )

        valid = sub_lags < len(acf)

        scores[valid] += (
            weight
            * acf[sub_lags[valid]]
        )

    # Find local maxima rather than blindly selecting the global maximum.
    peaks = []

    for i in range(1, len(scores) - 1):
        if (
            scores[i] >= scores[i - 1]
            and scores[i] >= scores[i + 1]
        ):
            peaks.append(i)

    if not peaks:
        peaks = [int(np.argmax(scores))]

    peaks.sort(
        key=lambda i: scores[i],
        reverse=True,
    )

    candidates = []

    for idx in peaks[:12]:
        fractional_lag = _interpolate_peak(
            scores,
            idx,
        )

        lag = float(lags[idx]) + (
            fractional_lag - float(idx)
        )

        if lag <= 0:
            continue

        bpm = (
            60.0
            * sr
            / hop
            / lag
        )

        # Generate octave-equivalent variants.
        variants = [
            bpm,
            bpm * 2.0,
            bpm / 2.0,
        ]

        for candidate_bpm in variants:
            if (
                MIN_BPM
                <= candidate_bpm
                <= MAX_BPM
            ):
                candidates.append(
                    (
                        float(candidate_bpm),
                        float(scores[idx]),
                    )
                )

    if not candidates:
        return [(120.0, 0.0)]

    # Merge nearly identical BPM candidates.
    candidates.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    merged = []

    for bpm, score in candidates:
        found = False

        for i, (existing_bpm, existing_score) in enumerate(merged):
            if abs(bpm - existing_bpm) < 1.5:
                if score > existing_score:
                    merged[i] = (
                        bpm,
                        score,
                    )
                found = True
                break

        if not found:
            merged.append(
                (bpm, score)
            )

    return merged[:8]


# ---------------------------------------------------------------------------
# Tempo detection
# ---------------------------------------------------------------------------

def _bandpass_spectrum(
    mag: np.ndarray,
    sr: int,
    frame_size: int,
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    """Keep only a frequency band from an STFT magnitude spectrogram."""
    if mag.size == 0:
        return mag

    freqs = np.fft.rfftfreq(
        frame_size,
        d=1.0 / sr,
    )

    mask = (
        (freqs >= low_hz)
        & (freqs <= high_hz)
    )

    return mag * mask[np.newaxis, :]


def _tempo_onset_envelope(
    mag: np.ndarray,
    sr: int,
    frame_size: int,
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    """Extract a beat-oriented onset envelope from a frequency band.

    Unlike the general onset envelope, this deliberately focuses on the
    frequency range where kick/snare transients are most useful for BPM
    estimation.
    """
    band_mag = _bandpass_spectrum(
        mag,
        sr,
        frame_size,
        low_hz,
        high_hz,
    )

    if band_mag.shape[0] < 2:
        return np.zeros(
            band_mag.shape[0],
            dtype=np.float64,
        )

    # Log compression prevents very loud hits from dominating.
    log_mag = np.log1p(
        band_mag
    )

    diff = np.diff(
        log_mag,
        axis=0,
    )

    positive_diff = np.maximum(
        diff,
        0.0,
    )

    # Equal weighting inside the selected band.
    flux = positive_diff.sum(
        axis=1
    )

    flux = np.concatenate(
        [[0.0], flux]
    )

    flux = np.maximum(
        flux,
        0.0,
    )

    # Remove slow spectral changes.
    if len(flux) >= 5:
        kernel = np.array(
            [1.0, 2.0, 3.0, 2.0, 1.0],
            dtype=np.float64,
        )
        kernel /= kernel.sum()

        smooth = np.convolve(
            flux,
            kernel,
            mode="same",
        )

        flux = np.maximum(
            flux - 0.40 * smooth,
            0.0,
        )

    # Robust normalization.
    median = np.median(
        flux
    )

    if median > 1e-12:
        flux /= median

    return flux


def _combine_beat_envelopes(
    kick_env: np.ndarray,
    snare_env: np.ndarray,
) -> np.ndarray:
    """Combine kick and snare onset envelopes.

    Kick is deliberately dominant because it provides the most stable
    quarter-note beat information for DJ-oriented BPM estimation.
    """
    n = min(
        len(kick_env),
        len(snare_env),
    )

    if n == 0:
        return np.zeros(
            0,
            dtype=np.float64,
        )

    kick = np.asarray(
        kick_env[:n],
        dtype=np.float64,
    )

    snare = np.asarray(
        snare_env[:n],
        dtype=np.float64,
    )

    # Independent normalization prevents one band from dominating simply
    # because it contains more spectral energy.
    kick_std = np.std(kick)

    if kick_std > 1e-12:
        kick = (
            kick - np.mean(kick)
        ) / kick_std

    snare_std = np.std(snare)

    if snare_std > 1e-12:
        snare = (
            snare - np.mean(snare)
        ) / snare_std

    # Kick is more important for the actual beat period.
    combined = (
        0.70 * kick
        + 0.30 * snare
    )

    combined = np.maximum(
        combined,
        0.0,
    )

    return combined


def _interpolate_peak(
    values: np.ndarray,
    index: int,
) -> float:
    """Parabolic interpolation around a discrete peak."""
    if (
        index <= 0
        or index >= len(values) - 1
    ):
        return float(index)

    y0 = float(
        values[index - 1]
    )
    y1 = float(
        values[index]
    )
    y2 = float(
        values[index + 1]
    )

    denom = (
        y0
        - 2.0 * y1
        + y2
    )

    if abs(denom) < 1e-12:
        return float(index)

    delta = (
        0.5
        * (y0 - y2)
        / denom
    )

    delta = max(
        -1.0,
        min(1.0, delta),
    )

    return (
        float(index)
        + delta
    )


def _tempo_candidates(
    onset_env: np.ndarray,
    sr: int,
    hop: int,
):
    """Return plausible BPM candidates from beat-oriented onset evidence."""
    if len(onset_env) < 16:
        return [(120.0, 0.0)]

    x = np.asarray(
        onset_env,
        dtype=np.float64,
    ).copy()

    x -= np.mean(x)

    std = np.std(x)

    if std < 1e-12:
        return [(120.0, 0.0)]

    x /= std

    # Very light smoothing. We want to preserve individual kick/snare hits.
    if len(x) >= 5:
        kernel = np.array(
            [1.0, 2.0, 3.0, 2.0, 1.0],
            dtype=np.float64,
        )
        kernel /= kernel.sum()

        x = np.convolve(
            x,
            kernel,
            mode="same",
        )

    n = len(x)

    fft_size = 1

    while fft_size < 2 * n:
        fft_size *= 2

    spectrum = np.fft.rfft(
        x,
        fft_size,
    )

    acf = np.fft.irfft(
        np.abs(spectrum) ** 2,
        fft_size,
    )[:n]

    if acf[0] <= 1e-12:
        return [(120.0, 0.0)]

    acf /= acf[0]

    min_lag = max(
        1,
        int(
            sr
            * 60.0
            / MAX_BPM
            / hop
        ),
    )

    max_lag = min(
        n - 1,
        int(
            sr
            * 60.0
            / MIN_BPM
            / hop
        ),
    )

    if max_lag <= min_lag:
        return [(120.0, 0.0)]

    lags = np.arange(
        min_lag,
        max_lag + 1,
    )

    base = acf[
        lags
    ].copy()

    # Rhythmic subdivision evidence.
    scores = base.copy()

    for harmonic, weight in (
        (2, 0.55),
        (3, 0.35),
        (4, 0.20),
    ):
        harmonic_lags = (
            lags * harmonic
        )

        valid = (
            harmonic_lags
            < len(acf)
        )

        scores[valid] += (
            weight
            * acf[
                harmonic_lags[valid]
            ]
        )

    # Also test half/double tempo relationships.
    for divisor, weight in (
        (2, 0.25),
        (3, 0.12),
    ):
        sub_lags = np.maximum(
            1,
            np.rint(
                lags / divisor
            ).astype(int),
        )

        valid = (
            sub_lags
            < len(acf)
        )

        scores[valid] += (
            weight
            * acf[
                sub_lags[valid]
            ]
        )

    peaks = []

    for i in range(
        1,
        len(scores) - 1,
    ):
        if (
            scores[i]
            >= scores[i - 1]
            and scores[i]
            >= scores[i + 1]
        ):
            peaks.append(i)

    if not peaks:
        peaks = [
            int(
                np.argmax(scores)
            )
        ]

    peaks.sort(
        key=lambda i: scores[i],
        reverse=True,
    )

    candidates = []

    for idx in peaks[:12]:
        fractional_lag = _interpolate_peak(
            scores,
            idx,
        )

        lag = (
            float(lags[idx])
            + (
                fractional_lag
                - float(idx)
            )
        )

        if lag <= 0:
            continue

        bpm = (
            60.0
            * sr
            / hop
            / lag
        )

        variants = [
            bpm,
            bpm * 2.0,
            bpm / 2.0,
        ]

        for candidate_bpm in variants:
            if (
                MIN_BPM
                <= candidate_bpm
                <= MAX_BPM
            ):
                candidates.append(
                    (
                        float(candidate_bpm),
                        float(scores[idx]),
                    )
                )

    if not candidates:
        return [(120.0, 0.0)]

    candidates.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    merged = []

    for bpm, score in candidates:
        found = False

        for i, (
            existing_bpm,
            existing_score,
        ) in enumerate(merged):

            if abs(
                bpm
                - existing_bpm
            ) < 1.5:

                if score > existing_score:
                    merged[i] = (
                        bpm,
                        score,
                    )

                found = True
                break

        if not found:
            merged.append(
                (
                    bpm,
                    score,
                )
            )

    return merged[:8]


def _estimate_tempo(
    onset_env: np.ndarray,
    sr: int,
    hop: int,
) -> float:
    """Estimate DJ-oriented BPM from kick/snare rhythmic evidence."""
    if len(onset_env) < 16:
        return 120.0

    x = np.asarray(
        onset_env,
        dtype=np.float64,
    ).copy()

    x -= np.median(x)

    std = np.std(x)

    if std < 1e-12:
        return 120.0

    x /= std

    if len(x) >= 5:
        x = np.convolve(
            x,
            np.array(
                [1.0, 2.0, 3.0, 2.0, 1.0]
            ) / 9.0,
            mode="same",
        )

    candidates = _tempo_candidates(
        x,
        sr,
        hop,
    )

    if not candidates:
        return 120.0

    def lag_score(
        lag: float,
    ) -> float:

        if (
            lag <= 1.0
            or lag >= len(x) - 2
        ):
            return -np.inf

        p = np.arange(
            lag,
            len(x) - 1,
            lag,
        )

        if len(p) < 4:
            return -np.inf

        i = np.floor(
            p
        ).astype(int)

        f = p - i

        v = (
            (1.0 - f) * x[i]
            + f * x[i + 1]
        )

        return float(
            np.mean(
                x[i] * v
            )
        )

    def refine(
        bpm: float,
    ) -> tuple[float, float]:

        period = (
            60.0
            * sr
            / (hop * bpm)
        )

        lags = np.linspace(
            period - 2.5,
            period + 2.5,
            21,
        )

        scores = np.array(
            [
                lag_score(lag)
                for lag in lags
            ]
        )

        if not np.any(
            np.isfinite(scores)
        ):
            return bpm, -np.inf

        i = int(
            np.nanargmax(scores)
        )

        lag = lags[i]

        if (
            0 < i
            < len(scores) - 1
        ):
            d = _interpolate_peak(
                scores,
                i,
            )

            lag = np.interp(
                d,
                np.arange(
                    len(lags)
                ),
                lags,
            )

        refined = (
            60.0
            * sr
            / (hop * lag)
        )

        while refined < MIN_BPM:
            refined *= 2.0

        while refined > MAX_BPM:
            refined /= 2.0

        return (
            float(refined),
            float(scores[i]),
        )

    refined = []

    for bpm, base_score in candidates:
        rbpm, rscore = refine(
            bpm
        )

        if np.isfinite(
            rscore
        ):
            score = (
                0.65 * float(base_score)
                + 0.35 * rscore
            )

            refined.append(
                (
                    rbpm,
                    score,
                )
            )

    if not refined:
        return float(
            np.clip(
                candidates[0][0],
                MIN_BPM,
                MAX_BPM,
            )
        )

    expanded = []

    for bpm, score in refined:

        for factor, weight in (
            (1.0, 1.00),
            (2.0, 0.88),
            (0.5, 0.88),
        ):
            b = bpm * factor

            if (
                MIN_BPM
                <= b
                <= MAX_BPM
            ):
                _, rhythmic_score = refine(
                    b
                )

                if np.isfinite(
                    rhythmic_score
                ):
                    expanded.append(
                        (
                            b,
                            0.70 * score
                            + 0.30
                            * rhythmic_score
                            * weight,
                        )
                    )

    scored = []

    for bpm, score in expanded:

        # Very mild DJ-oriented prior.
        prior = (
            0.035
            if 85.0 <= bpm <= 155.0
            else 0.0
        )

        scored.append(
            (
                bpm,
                score + prior,
            )
        )

    scored.sort(
        key=lambda z: z[1],
        reverse=True,
    )

    merged = []

    for bpm, score in scored:

        if any(
            abs(bpm - b) < 1.5
            for b, _ in merged
        ):
            continue

        merged.append(
            (
                bpm,
                score,
            )
        )

        if len(merged) >= 6:
            break

    if not merged:
        return 120.0

    best_bpm, best_score = merged[0]

    # Prefer candidates with consistent beat subdivisions when scores
    # are extremely close.
    for bpm, score in merged[1:]:

        if (
            score
            < best_score * 0.96
        ):
            continue

        period = (
            60.0
            * sr
            / (hop * bpm)
        )

        checks = [
            lag_score(
                period * k
            )
            for k in (
                1.0,
                2.0,
                3.0,
                4.0,
            )
        ]

        valid_checks = [
            s
            for s in checks
            if np.isfinite(s)
        ]

        support = (
            np.mean(valid_checks)
            if valid_checks
            else -np.inf
        )

        best_period = (
            60.0
            * sr
            / (hop * best_bpm)
        )

        best_checks = [
            lag_score(
                best_period * k
            )
            for k in (
                1.0,
                2.0,
                3.0,
                4.0,
            )
        ]

        best_valid_checks = [
            s
            for s in best_checks
            if np.isfinite(s)
        ]

        best_support = (
            np.mean(
                best_valid_checks
            )
            if best_valid_checks
            else -np.inf
        )

        if (
            support
            > best_support * 1.03
        ):
            best_bpm = bpm
            best_score = score

    return float(
        np.clip(
            best_bpm,
            MIN_BPM,
            MAX_BPM,
        )
    )
# ---------------------------------------------------------------------------
# Chroma / key detection
# ---------------------------------------------------------------------------

def _chroma_from_spectrum(
    mag: np.ndarray,
    sr: int,
    frame_size: int = KEY_FRAME_SIZE,
) -> np.ndarray:
    """Convert spectral energy into a 12-dimensional chroma vector.

    Uses logarithmic frequency weighting, soft pitch-class assignment and
    harmonic weighting. Very low frequencies are attenuated because bass
    fundamentals can otherwise dominate key estimation.
    """
    if mag.size == 0:
        return np.zeros(12)

    freqs = np.fft.rfftfreq(
        frame_size,
        d=1.0 / sr,
    )

    valid = (
        (freqs >= KEY_MIN_HZ)
        & (freqs <= KEY_MAX_HZ)
    )

    freqs_safe = np.maximum(
        freqs,
        1e-6,
    )

    with np.errstate(
        divide="ignore",
        invalid="ignore",
    ):
        midi = (
            69.0
            + 12.0
            * np.log2(
                freqs_safe / 440.0
            )
        )

    pitch_class_f = np.mod(
        midi,
        12.0,
    )

    # Average over frames instead of summing raw magnitude. This prevents
    # a single loud section from dominating the key.
    spectral = np.mean(
        mag,
        axis=0,
    )

    # Log compression.
    spectral = np.log1p(
        spectral
    )

    # Frequency-dependent weighting.
    weight = np.ones_like(
        freqs,
        dtype=np.float64,
    )

    low = freqs < 120.0
    mid = (
        (freqs >= 120.0)
        & (freqs <= 1200.0)
    )
    high = freqs > 1200.0

    weight[low] = 0.35
    weight[mid] = 1.0
    weight[high] = 0.75

    spectral *= weight
    spectral *= valid

    chroma = np.zeros(
        12,
        dtype=np.float64,
    )

    # Soft triangular pitch-class assignment.
    for pc in range(12):
        distance = np.abs(
            pitch_class_f - pc
        )

        distance = np.minimum(
            distance,
            12.0 - distance,
        )

        # A slightly wider kernel is more robust to detuning and spectral
        # leakage than a hard nearest-note assignment.
        assignment = np.exp(
            -0.5
            * (distance / 0.55) ** 2
        )

        chroma[pc] = np.sum(
            spectral
            * assignment
        )

    total = chroma.sum()

    if total > 1e-12:
        chroma /= total

    return chroma


def _key_profile_score(
    chroma: np.ndarray,
    profile: np.ndarray,
) -> float:
    """Combined correlation + cosine/profile-fit score."""
    c = np.asarray(
        chroma,
        dtype=np.float64,
    )

    p = np.asarray(
        profile,
        dtype=np.float64,
    )

    c_centered = c - c.mean()
    p_centered = p - p.mean()

    c_norm = np.linalg.norm(
        c_centered
    )

    p_norm = np.linalg.norm(
        p_centered
    )

    if (
        c_norm < 1e-12
        or p_norm < 1e-12
    ):
        correlation = 0.0
    else:
        correlation = float(
            np.dot(
                c_centered,
                p_centered,
            )
            / (
                c_norm
                * p_norm
            )
        )

    c_norm_raw = np.linalg.norm(c)
    p_norm_raw = np.linalg.norm(p)

    if (
        c_norm_raw < 1e-12
        or p_norm_raw < 1e-12
    ):
        cosine = 0.0
    else:
        cosine = float(
            np.dot(c, p)
            / (
                c_norm_raw
                * p_norm_raw
            )
        )

    return (
        0.70 * correlation
        + 0.30 * cosine
    )


def _detect_key(chroma: np.ndarray) -> str:
    """Detect musical key using an ensemble of key-profile scores."""
    chroma = np.asarray(
        chroma,
        dtype=np.float64,
    )

    if (
        chroma.shape != (12,)
        or not np.all(np.isfinite(chroma))
        or chroma.sum() <= 1e-12
    ):
        return normalize_key_name(
            "C",
            "major",
        )

    best_score = -np.inf
    best_key = (
        "C",
        "major",
    )

    for i in range(12):
        major_a = np.roll(
            MAJOR_PROFILE,
            i,
        )

        major_b = np.roll(
            MAJOR_PROFILE_ALT,
            i,
        )

        minor_a = np.roll(
            MINOR_PROFILE,
            i,
        )

        minor_b = np.roll(
            MINOR_PROFILE_ALT,
            i,
        )

        major_score = (
            0.65
            * _key_profile_score(
                chroma,
                major_a,
            )
            + 0.35
            * _key_profile_score(
                chroma,
                major_b,
            )
        )

        minor_score = (
            0.65
            * _key_profile_score(
                chroma,
                minor_a,
            )
            + 0.35
            * _key_profile_score(
                chroma,
                minor_b,
            )
        )

        if major_score > best_score:
            best_score = major_score
            best_key = (
                NOTE_NAMES[i],
                "major",
            )

        if minor_score > best_score:
            best_score = minor_score
            best_key = (
                NOTE_NAMES[i],
                "minor",
            )

    return normalize_key_name(
        *best_key
    )


def _detect_key_from_stft(
    key_mag: np.ndarray,
    sr: int,
    frame_size: int,
) -> str:
    """Segment-wise key detection with confidence-weighted voting."""
    if key_mag.shape[0] == 0:
        return normalize_key_name(
            "C",
            "major",
        )

    # Divide the track into several temporal regions. This avoids a vocal
    # intro, breakdown or outro completely determining the key.
    n_frames = key_mag.shape[0]

    n_segments = min(
        12,
        max(
            4,
            n_frames // 8,
        ),
    )

    boundaries = np.linspace(
        0,
        n_frames,
        n_segments + 1,
        dtype=int,
    )

    key_votes = np.zeros(
        24,
        dtype=np.float64,
    )

    for segment_index in range(
        n_segments
    ):
        start = boundaries[
            segment_index
        ]

        end = boundaries[
            segment_index + 1
        ]

        if end <= start:
            continue

        segment_mag = key_mag[
            start:end
        ]

        chroma = _chroma_from_spectrum(
            segment_mag,
            sr,
            frame_size,
        )

        if chroma.sum() <= 1e-12:
            continue

        scores = []

        for i in range(12):
            major = np.roll(
                MAJOR_PROFILE,
                i,
            )

            minor = np.roll(
                MINOR_PROFILE,
                i,
            )

            major_score = _key_profile_score(
                chroma,
                major,
            )

            minor_score = _key_profile_score(
                chroma,
                minor,
            )

            scores.append(
                major_score
            )

            scores.append(
                minor_score
            )

        scores = np.asarray(
            scores,
            dtype=np.float64,
        )

        # Segment confidence is based on the separation between the first
        # and second candidates.
        ordered = np.sort(
            scores
        )

        confidence = (
            max(
                0.05,
                ordered[-1]
                - ordered[-2],
            )
            if len(ordered) >= 2
            else 0.05
        )

        # Longer segments receive slightly more weight.
        duration_weight = (
            end - start
        ) / max(
            1,
            n_frames,
        )

        weight = (
            confidence
            * (
                0.5
                + duration_weight
            )
        )

        # Add a soft vote to the strongest candidates rather than using only
        # one hard decision.
        best_indices = np.argsort(
            scores
        )[-3:]

        for rank, key_index in enumerate(
            best_indices
        ):
            rank_weight = (
                1.0
                if rank == 2
                else 0.55
                if rank == 1
                else 0.25
            )

            key_votes[
                key_index
            ] += (
                weight
                * rank_weight
            )

    if key_votes.max() <= 0:
        chroma = _chroma_from_spectrum(
            key_mag,
            sr,
            frame_size,
        )

        return _detect_key(
            chroma
        )

    best_index = int(
        np.argmax(
            key_votes
        )
    )

    note_index = best_index // 2
    mode_index = best_index % 2

    mode = (
        "major"
        if mode_index == 0
        else "minor"
    )

    return normalize_key_name(
        NOTE_NAMES[note_index],
        mode,
    )


# ---------------------------------------------------------------------------
# Loudness
# ---------------------------------------------------------------------------

def _biquad_filter(
    y: np.ndarray,
    b: tuple,
    a: tuple,
) -> np.ndarray:
    """Simple direct-form-II transposed biquad implementation."""
    if len(y) == 0:
        return y.astype(
            np.float64
        )

    b0, b1, b2 = b
    a0, a1, a2 = a

    if abs(a0) < 1e-12:
        return y.astype(
            np.float64
        )

    b0 /= a0
    b1 /= a0
    b2 /= a0
    a1 /= a0
    a2 /= a0

    x = np.asarray(
        y,
        dtype=np.float64,
    )

    output = np.empty_like(x)

    z1 = 0.0
    z2 = 0.0

    for i, sample in enumerate(x):
        value = (
            b0 * sample
            + z1
        )

        z1 = (
            b1 * sample
            - a1 * value
            + z2
        )

        z2 = (
            b2 * sample
            - a2 * value
        )

        output[i] = value

    return output


def _k_weighted_signal(
    y: np.ndarray,
    sr: int,
) -> np.ndarray:
    """Approximate BS.1770 K-weighting using two biquad stages.

    The coefficients are calculated for the actual analysis sample rate.
    """
    if len(y) == 0:
        return np.asarray(
            y,
            dtype=np.float64,
        )

    # High-shelf stage.
    f0 = 1681.974450955533
    G = 3.999843853973347
    Q = 0.7071752369554196

    A = 10.0 ** (G / 40.0)
    omega = 2.0 * np.pi * f0 / sr
    alpha = np.sin(omega) / (2.0 * Q)
    cos_omega = np.cos(omega)
    sin_omega = np.sin(omega)

    beta = 2.0 * np.sqrt(A) * alpha

    b0 = (
        A
        * (
            (A + 1)
            + (A - 1) * cos_omega
            + beta
        )
    )

    b1 = (
        -2.0
        * A
        * (
            (A - 1)
            + (A + 1) * cos_omega
        )
    )

    b2 = (
        A
        * (
            (A + 1)
            + (A - 1) * cos_omega
            - beta
        )
    )

    a0 = (
        (A + 1)
        - (A - 1) * cos_omega
        + beta
    )

    a1 = (
        2.0
        * (
            (A - 1)
            - (A + 1) * cos_omega
        )
    )

    a2 = (
        (A + 1)
        - (A - 1) * cos_omega
        - beta
    )

    high_shelf = _biquad_filter(
        y,
        (b0, b1, b2),
        (a0, a1, a2),
    )

    # High-pass stage.
    f0 = 38.13547087613982
    Q = 0.5003270373238773

    omega = 2.0 * np.pi * f0 / sr
    alpha = np.sin(omega) / (2.0 * Q)
    cos_omega = np.cos(omega)

    b0 = (1.0 + cos_omega) / 2.0
    b1 = -(1.0 + cos_omega)
    b2 = (1.0 + cos_omega) / 2.0

    a0 = 1.0 + alpha
    a1 = -2.0 * cos_omega
    a2 = 1.0 - alpha

    return _biquad_filter(
        high_shelf,
        (b0, b1, b2),
        (a0, a1, a2),
    )


def _estimate_loudness(
    y: np.ndarray,
    sr: int,
) -> float:
    """Estimate integrated loudness in LUFS.

    This is a lightweight approximation of BS.1770. It uses K-weighting,
    400 ms blocks and relative-gating behavior while remaining dependency-free.
    """
    if len(y) == 0:
        return LOUDNESS_MIN_DB

    signal = np.asarray(
        y,
        dtype=np.float64,
    )

    weighted = _k_weighted_signal(
        signal,
        sr,
    )

    block_size = max(
        1,
        int(0.400 * sr),
    )

    hop_size = max(
        1,
        int(0.100 * sr),
    )

    if len(weighted) < block_size:
        mean_square = float(
            np.mean(
                weighted ** 2
            )
        )

        if mean_square <= 1e-15:
            return LOUDNESS_MIN_DB

        # Approximate LUFS calibration.
        loudness = (
            -0.691
            + 10.0
            * np.log10(
                mean_square
            )
        )

        return float(
            max(
                LOUDNESS_MIN_DB,
                min(
                    LOUDNESS_MAX_DB,
                    loudness,
                ),
            )
        )

    powers = []

    for start in range(
        0,
        len(weighted)
        - block_size
        + 1,
        hop_size,
    ):
        block = weighted[
            start:start + block_size
        ]

        power = float(
            np.mean(
                block ** 2
            )
        )

        if power > 1e-15:
            powers.append(power)

    if not powers:
        return LOUDNESS_MIN_DB

    powers = np.asarray(
        powers,
        dtype=np.float64,
    )

    block_lufs = (
        -0.691
        + 10.0
        * np.log10(
            np.maximum(
                powers,
                1e-15,
            )
        )
    )

    # Absolute gate.
    gated = block_lufs[
        block_lufs >= -70.0
    ]

    if len(gated) == 0:
        return LOUDNESS_MIN_DB

    # First estimate for the relative gate.
    ungated_power = np.mean(
        10.0 ** (
            (gated + 0.691) / 10.0
        )
    )

    ungated_lufs = (
        -0.691
        + 10.0
        * np.log10(
            max(
                ungated_power,
                1e-15,
            )
        )
    )

    relative_gate = (
        ungated_lufs - 10.0
    )

    gated = gated[
        gated >= relative_gate
    ]

    if len(gated) == 0:
        gated = block_lufs[
            block_lufs >= -70.0
        ]

    final_power = np.mean(
        10.0 ** (
            (gated + 0.691) / 10.0
        )
    )

    loudness = (
        -0.691
        + 10.0
        * np.log10(
            max(
                final_power,
                1e-15,
            )
        )
    )

    return float(
        max(
            LOUDNESS_MIN_DB,
            min(
                LOUDNESS_MAX_DB,
                loudness,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Spectral / musical descriptors
# ---------------------------------------------------------------------------

def _spectral_features(
    mag: np.ndarray,
    sr: int,
    frame_size: int,
) -> dict:
    """Extract normalized descriptors used by energy and genre detection."""
    if mag.size == 0:
        return {
            "spectral_centroid": 0.0,
            "spectral_rolloff": 0.0,
            "spectral_flatness": 0.0,
            "bass_ratio": 0.0,
            "low_mid_ratio": 0.0,
            "mid_ratio": 0.0,
            "high_ratio": 0.0,
            "brightness": 0.0,
            "harmonicity": 0.0,
        }

    freqs = np.fft.rfftfreq(
        frame_size,
        d=1.0 / sr,
    )

    power = mag ** 2

    frame_energy = (
        power.sum(axis=1)
        + 1e-12
    )

    # Average frame spectrum.
    mean_power = np.mean(
        power,
        axis=0,
    )

    total_power = (
        mean_power.sum()
        + 1e-12
    )

    centroid = float(
        np.sum(
            freqs
            * mean_power
        )
        / total_power
    )

    cumulative = np.cumsum(
        mean_power
    )

    rolloff_target = (
        0.85
        * cumulative[-1]
    )

    rolloff_index = int(
        np.searchsorted(
            cumulative,
            rolloff_target,
        )
    )

    rolloff_index = min(
        rolloff_index,
        len(freqs) - 1,
    )

    rolloff = float(
        freqs[
            rolloff_index
        ]
    )

    # Spectral flatness.
    log_power = np.log(
        np.maximum(
            mean_power,
            1e-12,
        )
    )

    geometric_mean = np.exp(
        np.mean(log_power)
    )

    arithmetic_mean = np.mean(
        mean_power
    ) + 1e-12

    flatness = float(
        geometric_mean
        / arithmetic_mean
    )

    def band_energy(
        low_hz,
        high_hz,
    ):
        mask = (
            (freqs >= low_hz)
            & (freqs < high_hz)
        )

        return float(
            mean_power[mask].sum()
            / total_power
        )

    bass_ratio = band_energy(
        20,
        150,
    )

    low_mid_ratio = band_energy(
        150,
        400,
    )

    mid_ratio = band_energy(
        400,
        2500,
    )

    high_ratio = band_energy(
        2500,
        min(
            10000,
            sr / 2,
        ),
    )

    brightness = (
        centroid
        / max(
            1.0,
            sr / 2,
        )
    )

    # Harmonicity approximation: compare the energy concentrated around
    # low-frequency harmonic peaks against the total spectrum.
    harmonicity_values = []

    for frame in mag[
        ::max(
            1,
            len(mag) // 80,
        )
    ]:
        if frame.sum() <= 1e-12:
            continue

        spectrum = frame

        # Local maxima.
        if len(spectrum) < 5:
            continue

        local_max = (
            (spectrum[1:-1] > spectrum[:-2])
            & (spectrum[1:-1] >= spectrum[2:])
        )

        peak_values = spectrum[
            1:-1
        ][local_max]

        if len(peak_values) == 0:
            harmonicity_values.append(
                0.0
            )
            continue

        sorted_peaks = np.sort(
            peak_values
        )

        top_count = min(
            12,
            len(sorted_peaks),
        )

        top_energy = np.sum(
            sorted_peaks[
                -top_count:
            ] ** 2
        )

        total = np.sum(
            spectrum ** 2
        ) + 1e-12

        harmonicity_values.append(
            float(
                top_energy / total
            )
        )

    harmonicity = float(
        np.mean(
            harmonicity_values
        )
        if harmonicity_values
        else 0.0
    )

    return {
        "spectral_centroid": centroid,
        "spectral_rolloff": rolloff,
        "spectral_flatness": flatness,
        "bass_ratio": bass_ratio,
        "low_mid_ratio": low_mid_ratio,
        "mid_ratio": mid_ratio,
        "high_ratio": high_ratio,
        "brightness": brightness,
        "harmonicity": harmonicity,
    }


def _rhythmic_features(
    onset_env: np.ndarray,
) -> dict:
    """Extract onset density, variability and rhythmic regularity."""
    if len(onset_env) == 0:
        return {
            "onset_density": 0.0,
            "onset_variability": 0.0,
            "rhythmic_regularity": 0.0,
        }

    x = np.maximum(
        np.asarray(
            onset_env,
            dtype=np.float64,
        ),
        0.0,
    )

    if x.max() > 0:
        threshold = (
            np.percentile(
                x,
                65,
            )
        )

        active = (
            x > threshold
        )

        onset_density = float(
            np.mean(active)
        )
    else:
        onset_density = 0.0

    onset_variability = float(
        np.std(x)
        / (
            np.mean(x)
            + 1e-12
        )
    )

    # Rhythmic regularity from autocorrelation around short beat periods.
    x_centered = (
        x - x.mean()
    )

    if (
        np.linalg.norm(
            x_centered
        )
        > 1e-12
    ):
        acf = np.correlate(
            x_centered,
            x_centered,
            mode="full",
        )

        acf = acf[
            len(x) - 1:
        ]

        if acf[0] > 1e-12:
            acf /= acf[0]

            upper = min(
                len(acf),
                100,
            )

            rhythmic_regularity = float(
                np.max(
                    acf[
                        2:upper
                    ]
                )
                if upper > 3
                else 0.0
            )
        else:
            rhythmic_regularity = 0.0
    else:
        rhythmic_regularity = 0.0

    return {
        "onset_density": onset_density,
        "onset_variability": onset_variability,
        "rhythmic_regularity": rhythmic_regularity,
    }


def _zero_crossing_rate(
    y: np.ndarray,
) -> float:
    """Calculate normalized zero-crossing rate."""
    if len(y) < 2:
        return 0.0

    crossings = np.count_nonzero(
        (
            y[:-1] >= 0
        )
        != (
            y[1:] >= 0
        )
    )

    return float(
        crossings
        / (
            len(y) - 1
        )
    )


# ---------------------------------------------------------------------------
# Genre classification
# ---------------------------------------------------------------------------

def _clean_genre_tag(genre: str) -> str:
    """Normalize common metadata genre strings."""
    if not genre:
        return ""

    genre = str(
        genre
    ).strip()

    if not genre:
        return ""

    aliases = {
        "edm": "Electronic",
        "electronica": "Electronic",
        "hip hop": "Hip-Hop",
        "hip-hop": "Hip-Hop",
        "hiphop": "Hip-Hop",
        "r&b": "R&B",
        "rnb": "R&B",
        "drum and bass": "Drum & Bass",
        "drum n bass": "Drum & Bass",
        "dnb": "Drum & Bass",
        "d&b": "Drum & Bass",
        "uk garage": "UK Garage",
        "ukg": "UK Garage",
        "deep house": "Deep House",
        "tech house": "Tech House",
        "minimal techno": "Minimal Techno",
        "hard techno": "Hard Techno",
        "hardstyle": "Hardstyle",
        "progressive house": "Progressive House",
        "trance": "Trance",
        "dubstep": "Dubstep",
        "breakbeat": "Breakbeat",
        "breaks": "Breakbeat",
    }

    return aliases.get(
        genre.lower(),
        genre,
    )


def _estimate_genre(
    tags: dict,
    tempo: float,
) -> str:
    """Estimate genre using BPM families plus spectral/rhythmic descriptors.

    BPM is used as a strong prior rather than as the sole classifier.
    Genres with incompatible tempo ranges are strongly penalized, while
    spectral and rhythmic features distinguish genres within each family.
    """

    metadata_genre = _clean_genre_tag(
        tags.get("genre", "")
    )

    if metadata_genre:
        return metadata_genre

    features = tags.get(
        "_genre_features",
        {},
    )

    bpm = float(
        tempo
        if tempo > 0
        else features.get("tempo", 120.0)
    )

    centroid = float(
        features.get("spectral_centroid", 2000.0)
    )

    rolloff = float(
        features.get("spectral_rolloff", 5000.0)
    )

    flatness = float(
        features.get("spectral_flatness", 0.3)
    )

    bass = float(
        features.get("bass_ratio", 0.2)
    )

    low_mid = float(
        features.get("low_mid_ratio", 0.2)
    )

    mid = float(
        features.get("mid_ratio", 0.4)
    )

    high = float(
        features.get("high_ratio", 0.2)
    )

    brightness = float(
        features.get("brightness", 0.15)
    )

    harmonicity = float(
        features.get("harmonicity", 0.1)
    )

    onset_density = float(
        features.get("onset_density", 0.2)
    )

    onset_variability = float(
        features.get("onset_variability", 1.0)
    )

    rhythmic_regularity = float(
        features.get("rhythmic_regularity", 0.5)
    )

    zcr = float(
        features.get("zero_crossing_rate", 0.05)
    )

    loudness = float(
        features.get("loudness", -14.0)
    )

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------

    scores = {}

    def add(name: str, score: float):
        scores[name] = scores.get(name, 0.0) + max(
            0.0,
            float(score),
        )

    def in_range(
        value: float,
        low: float,
        high: float,
        weight: float,
    ) -> float:
        return weight if low <= value <= high else 0.0

    # ---------------------------------------------------------------
    # Tempo families
    #
    # These are deliberately broad. They are used as strong priors,
    # not absolute rules.
    # ---------------------------------------------------------------

    is_slow = bpm < 100
    is_mid = 100 <= bpm < 118
    is_house = 116 <= bpm <= 134
    is_fast_house = 128 <= bpm <= 140
    is_fast_electronic = 135 <= bpm <= 160
    is_dnb = 155 <= bpm <= 195

    # ---------------------------------------------------------------
    # House
    # ---------------------------------------------------------------

    house = 0.0

    house += in_range(
        bpm,
        118,
        130,
        4.0,
    )

    house += in_range(
        bpm,
        113,
        117.9,
        1.2,
    )

    house += in_range(
        bpm,
        130.1,
        134,
        1.2,
    )

    if bass >= 0.16:
        house += 0.8

    if 0.10 <= onset_density <= 0.45:
        house += 0.8

    if flatness < 0.45:
        house += 0.4

    if rhythmic_regularity > 0.45:
        house += 0.5

    add(
        "House",
        house,
    )

    # ---------------------------------------------------------------
    # Deep House
    #
    # Do NOT inherit the House score.
    # ---------------------------------------------------------------

    deep_house = 0.0

    deep_house += in_range(
        bpm,
        118,
        126,
        4.5,
    )

    deep_house += in_range(
        bpm,
        126,
        130,
        1.0,
    )

    if bass >= 0.20:
        deep_house += 1.2

    if centroid < 2600:
        deep_house += 1.0

    if high < 0.25:
        deep_house += 0.7

    if harmonicity > 0.08:
        deep_house += 0.5

    if flatness < 0.35:
        deep_house += 0.4

    # Strong penalty outside the normal Deep House range.
    if bpm > 132:
        deep_house *= 0.05

    if bpm < 112:
        deep_house *= 0.15

    add(
        "Deep House",
        deep_house,
    )

    # ---------------------------------------------------------------
    # Tech House
    # ---------------------------------------------------------------

    tech_house = 0.0

    tech_house += in_range(
        bpm,
        124,
        130,
        4.0,
    )

    if bass >= 0.16:
        tech_house += 0.9

    if onset_density >= 0.18:
        tech_house += 0.9

    if flatness >= 0.18:
        tech_house += 0.7

    if centroid >= 1800:
        tech_house += 0.6

    if rhythmic_regularity > 0.40:
        tech_house += 0.4

    if bpm > 136:
        tech_house *= 0.05

    add(
        "Tech House",
        tech_house,
    )

    # ---------------------------------------------------------------
    # Progressive House
    # ---------------------------------------------------------------

    progressive = 0.0

    progressive += in_range(
        bpm,
        120,
        132,
        3.5,
    )

    if onset_variability < 2.5:
        progressive += 0.8

    if harmonicity > 0.10:
        progressive += 0.8

    if centroid < 3500:
        progressive += 0.5

    if rolloff > 4500:
        progressive += 0.5

    if rhythmic_regularity > 0.45:
        progressive += 0.4

    if bpm > 138:
        progressive *= 0.05

    add(
        "Progressive House",
        progressive,
    )

    # ---------------------------------------------------------------
    # Minimal Techno
    # ---------------------------------------------------------------

    minimal = 0.0

    minimal += in_range(
        bpm,
        120,
        132,
        3.2,
    )

    if bass >= 0.15:
        minimal += 0.9

    if onset_density < 0.28:
        minimal += 1.0

    if flatness < 0.35:
        minimal += 0.5

    if centroid < 3000:
        minimal += 0.6

    if rhythmic_regularity > 0.50:
        minimal += 0.5

    if bpm > 138:
        minimal *= 0.05

    add(
        "Minimal Techno",
        minimal,
    )

    # ---------------------------------------------------------------
    # Techno
    # ---------------------------------------------------------------

    techno = 0.0

    techno += in_range(
        bpm,
        125,
        140,
        3.5,
    )

    if flatness > 0.18:
        techno += 0.9

    if onset_density >= 0.20:
        techno += 0.9

    if harmonicity < 0.18:
        techno += 0.7

    if centroid >= 1800:
        techno += 0.6

    if rhythmic_regularity > 0.45:
        techno += 0.5

    # Techno remains plausible above 140, but gets less suitable
    # compared with Hard Techno.
    if bpm > 145:
        techno *= 0.35

    add(
        "Techno",
        techno,
    )

    # ---------------------------------------------------------------
    # Hard Techno
    # ---------------------------------------------------------------

    hard_techno = 0.0

    hard_techno += in_range(
        bpm,
        140,
        160,
        4.5,
    )

    hard_techno += in_range(
        bpm,
        135,
        139.9,
        1.5,
    )

    if brightness > 0.10:
        hard_techno += 0.8

    if flatness > 0.20:
        hard_techno += 0.8

    if onset_density > 0.20:
        hard_techno += 1.0

    if centroid >= 1800:
        hard_techno += 0.5

    if rhythmic_regularity > 0.40:
        hard_techno += 0.4

    add(
        "Hard Techno",
        hard_techno,
    )

    # ---------------------------------------------------------------
    # Trance
    # ---------------------------------------------------------------

    trance = 0.0

    trance += in_range(
        bpm,
        128,
        145,
        4.0,
    )

    if harmonicity > 0.12:
        trance += 1.2

    if high > 0.18:
        trance += 0.8

    if rolloff > 5000:
        trance += 0.8

    if centroid > 2500:
        trance += 0.6

    if brightness > 0.15:
        trance += 0.5

    if rhythmic_regularity > 0.45:
        trance += 0.4

    add(
        "Trance",
        trance,
    )

    # ---------------------------------------------------------------
    # UK Garage
    # ---------------------------------------------------------------

    ukg = 0.0

    ukg += in_range(
        bpm,
        128,
        145,
        3.5,
    )

    if 135 <= bpm <= 145:
        ukg += 1.0

    if bass >= 0.15:
        ukg += 0.9

    if onset_variability > 1.2:
        ukg += 1.0

    if harmonicity > 0.07:
        ukg += 0.5

    if rhythmic_regularity < 0.55:
        ukg += 0.5

    add(
        "UK Garage",
        ukg,
    )

    # ---------------------------------------------------------------
    # Breakbeat
    # ---------------------------------------------------------------

    breakbeat = 0.0

    breakbeat += in_range(
        bpm,
        110,
        145,
        2.5,
    )

    if onset_variability > 1.4:
        breakbeat += 1.5

    if rhythmic_regularity < 0.70:
        breakbeat += 1.0

    if bass > 0.12:
        breakbeat += 0.5

    if onset_density > 0.18:
        breakbeat += 0.4

    add(
        "Breakbeat",
        breakbeat,
    )

    # ---------------------------------------------------------------
    # Dubstep
    # ---------------------------------------------------------------

    dubstep = 0.0

    dubstep += in_range(
        bpm,
        135,
        150,
        3.5,
    )

    if bass >= 0.22:
        dubstep += 1.5

    if onset_variability > 1.5:
        dubstep += 1.0

    if centroid < 2500:
        dubstep += 0.7

    if flatness > 0.20:
        dubstep += 0.5

    if rhythmic_regularity < 0.60:
        dubstep += 0.6

    add(
        "Dubstep",
        dubstep,
    )

    # ---------------------------------------------------------------
    # Drum & Bass
    # ---------------------------------------------------------------

    dnb = 0.0

    dnb += in_range(
        bpm,
        160,
        190,
        4.5,
    )

    dnb += in_range(
        bpm,
        155,
        159.9,
        1.5,
    )

    if bass >= 0.18:
        dnb += 0.8

    if onset_density >= 0.25:
        dnb += 1.0

    if onset_variability > 1.5:
        dnb += 0.8

    if flatness > 0.20:
        dnb += 0.5

    if rhythmic_regularity < 0.65:
        dnb += 0.5

    add(
        "Drum & Bass",
        dnb,
    )

    # ---------------------------------------------------------------
    # Hardstyle
    # ---------------------------------------------------------------

    hardstyle = 0.0

    hardstyle += in_range(
        bpm,
        145,
        155,
        4.0,
    )

    if bpm >= 150:
        hardstyle += 0.8

    if bass >= 0.20:
        hardstyle += 0.8

    if brightness > 0.12:
        hardstyle += 0.8

    if flatness > 0.22:
        hardstyle += 0.8

    if onset_density > 0.25:
        hardstyle += 0.8

    if centroid > 2200:
        hardstyle += 0.5

    add(
        "Hardstyle",
        hardstyle,
    )

    # ---------------------------------------------------------------
    # Hip-Hop
    # ---------------------------------------------------------------

    hiphop = 0.0

    hiphop += in_range(
        bpm,
        65,
        105,
        3.5,
    )

    if bass >= 0.18:
        hiphop += 0.9

    if harmonicity > 0.10:
        hiphop += 0.7

    if centroid < 2800:
        hiphop += 0.6

    if onset_variability > 1.0:
        hiphop += 0.4

    add(
        "Hip-Hop",
        hiphop,
    )

    # ---------------------------------------------------------------
    # R&B
    # ---------------------------------------------------------------

    rnb = 0.0

    rnb += in_range(
        bpm,
        65,
        110,
        3.0,
    )

    if harmonicity > 0.15:
        rnb += 1.2

    if centroid < 3000:
        rnb += 0.8

    if bass >= 0.15:
        rnb += 0.7

    if flatness < 0.35:
        rnb += 0.5

    add(
        "R&B",
        rnb,
    )

    # ---------------------------------------------------------------
    # Downtempo
    # ---------------------------------------------------------------

    downtempo = 0.0

    downtempo += in_range(
        bpm,
        60,
        100,
        3.0,
    )

    if centroid < 2200:
        downtempo += 0.9

    if onset_density < 0.20:
        downtempo += 1.0

    if harmonicity > 0.10:
        downtempo += 0.5

    if flatness < 0.30:
        downtempo += 0.4

    add(
        "Downtempo",
        downtempo,
    )

    # ---------------------------------------------------------------
    # Ambient
    # ---------------------------------------------------------------

    ambient = 0.0

    ambient += in_range(
        bpm,
        40,
        100,
        2.0,
    )

    if onset_density < 0.12:
        ambient += 1.8

    if harmonicity > 0.15:
        ambient += 1.0

    if flatness < 0.25:
        ambient += 0.7

    if onset_variability < 2.0:
        ambient += 0.5

    if centroid < 2500:
        ambient += 0.5

    add(
        "Ambient",
        ambient,
    )

    # ---------------------------------------------------------------
    # Pop
    # ---------------------------------------------------------------

    pop = 0.0

    pop += in_range(
        bpm,
        90,
        135,
        2.0,
    )

    if harmonicity > 0.10:
        pop += 1.0

    if 0.15 <= flatness <= 0.45:
        pop += 0.7

    if 0.12 <= high <= 0.35:
        pop += 0.6

    if loudness > -10:
        pop += 0.4

    if centroid > 1800:
        pop += 0.4

    add(
        "Pop",
        pop,
    )

    # ---------------------------------------------------------------
    # Rock
    # ---------------------------------------------------------------

    rock = 0.0

    rock += in_range(
        bpm,
        90,
        150,
        1.5,
    )

    if harmonicity > 0.14:
        rock += 1.2

    if centroid > 2200:
        rock += 0.8

    if zcr > 0.06:
        rock += 0.7

    if high > 0.15:
        rock += 0.6

    if flatness > 0.15:
        rock += 0.4

    add(
        "Rock",
        rock,
    )

    # ---------------------------------------------------------------
    # Electronic
    #
    # Generic fallback for electronic music that does not fit one of
    # the more specific electronic genres.
    # ---------------------------------------------------------------

    electronic = 0.0

    if 100 <= bpm <= 180:
        electronic += 1.5

    if bass >= 0.12:
        electronic += 0.7

    if onset_density >= 0.15:
        electronic += 0.7

    if flatness >= 0.15:
        electronic += 0.6

    if centroid >= 1500:
        electronic += 0.5

    if rhythmic_regularity > 0.35:
        electronic += 0.4

    add(
        "Electronic",
        electronic,
    )

    # ---------------------------------------------------------------
    # BPM-family penalties
    #
    # These prevent a spectrally dark 145 BPM track from accidentally
    # becoming Deep House, for example.
    # ---------------------------------------------------------------

    def multiply(name: str, factor: float):
        if name in scores:
            scores[name] *= factor

    # < 110 BPM
    if bpm < 110:
        multiply("House", 0.10)
        multiply("Deep House", 0.10)
        multiply("Tech House", 0.05)
        multiply("Progressive House", 0.10)
        multiply("Minimal Techno", 0.05)
        multiply("Techno", 0.05)
        multiply("Hard Techno", 0.01)
        multiply("Trance", 0.05)
        multiply("UK Garage", 0.05)
        multiply("Breakbeat", 0.40)
        multiply("Dubstep", 0.05)
        multiply("Drum & Bass", 0.01)
        multiply("Hardstyle", 0.01)

    # 110-116 BPM
    elif bpm < 116:
        multiply("Deep House", 0.35)
        multiply("Tech House", 0.20)
        multiply("Minimal Techno", 0.15)
        multiply("Hard Techno", 0.01)
        multiply("Drum & Bass", 0.01)
        multiply("Hardstyle", 0.01)

    # 116-134 BPM: house/electronic territory
    elif bpm <= 134:
        multiply("Hard Techno", 0.05)
        multiply("Hardstyle", 0.01)
        multiply("Drum & Bass", 0.01)

    # 135-139 BPM
    elif bpm < 140:
        multiply("Deep House", 0.03)
        multiply("Tech House", 0.10)
        multiply("Progressive House", 0.10)
        multiply("Minimal Techno", 0.15)
        multiply("House", 0.20)

    # 140-144 BPM
    elif bpm < 145:
        multiply("Deep House", 0.01)
        multiply("House", 0.03)
        multiply("Tech House", 0.03)
        multiply("Progressive House", 0.03)
        multiply("Minimal Techno", 0.05)

    # 145-154 BPM
    elif bpm < 155:
        multiply("Deep House", 0.005)
        multiply("House", 0.01)
        multiply("Tech House", 0.01)
        multiply("Progressive House", 0.01)
        multiply("Minimal Techno", 0.02)

    # 155-159 BPM
    elif bpm < 160:
        multiply("Deep House", 0.001)
        multiply("House", 0.001)
        multiply("Tech House", 0.001)
        multiply("Progressive House", 0.001)
        multiply("Minimal Techno", 0.005)

    # 160+ BPM: DnB / high-tempo electronic
    else:
        multiply("Deep House", 0.001)
        multiply("House", 0.001)
        multiply("Tech House", 0.001)
        multiply("Progressive House", 0.001)
        multiply("Minimal Techno", 0.001)

    # ---------------------------------------------------------------
    # Additional family-specific adjustments
    # ---------------------------------------------------------------

    # At 140+ BPM, Hard Techno / Trance / Dubstep / Hardstyle should
    # generally be more competitive than House-family genres.
    if 140 <= bpm <= 160:
        if brightness > 0.10:
            scores["Hard Techno"] *= 1.10

        if flatness > 0.20:
            scores["Hard Techno"] *= 1.10

        if harmonicity > 0.12:
            scores["Trance"] *= 1.10

        if bass > 0.22:
            scores["Dubstep"] *= 1.10

    # Strong breakbeat characteristics.
    if 110 <= bpm <= 150:
        if rhythmic_regularity < 0.55:
            scores["Breakbeat"] *= 1.15

        if onset_variability > 1.4:
            scores["Breakbeat"] *= 1.15

    # Strong DnB characteristics.
    if 155 <= bpm <= 195:
        if onset_density > 0.25:
            scores["Drum & Bass"] *= 1.15

        if onset_variability > 1.5:
            scores["Drum & Bass"] *= 1.10

    # ---------------------------------------------------------------
    # Select result
    # ---------------------------------------------------------------

    if not scores:
        return "Unknown"

    ranked = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    best_genre, best_score = ranked[0]

    # Generic electronic fallback.
    if best_score < 2.0:
        if bpm >= 160:
            return "Drum & Bass"

        if bpm >= 140:
            return "Electronic"

        if bpm >= 115:
            return "Electronic"

        if bpm >= 95:
            return "Pop"

        return "Downtempo"

    return best_genre


# ---------------------------------------------------------------------------
# Waveform
# ---------------------------------------------------------------------------

def _compute_waveform_bands(
    y: np.ndarray,
    sr: int,
    num_points: int = WAVEFORM_POINTS,
):
    """Measure low/high spectral energy for a compact waveform preview."""
    if len(y) == 0:
        return (
            [0.0] * num_points,
            [0.0] * num_points,
        )

    seg_len = max(
        8,
        len(y) // num_points,
    )

    lows = []
    highs = []

    window = np.hanning(
        seg_len
    ).astype(
        np.float32
    )

    for i in range(
        num_points
    ):
        seg = y[
            i * seg_len:
            i * seg_len + seg_len
        ]

        if len(seg) < 8:
            lows.append(0.0)
            highs.append(0.0)
            continue

        if len(seg) < seg_len:
            seg = np.pad(
                seg,
                (
                    0,
                    seg_len - len(seg),
                ),
            )

        spectrum = np.abs(
            np.fft.rfft(
                seg * window
            )
        )

        freqs = np.fft.rfftfreq(
            seg_len,
            d=1.0 / sr,
        )

        lows.append(
            float(
                spectrum[
                    freqs
                    < WAVEFORM_LOW_HZ
                ].sum()
            )
        )

        highs.append(
            float(
                spectrum[
                    freqs
                    > WAVEFORM_HIGH_HZ
                ].sum()
            )
        )

    lows_arr = np.asarray(
        lows,
        dtype=np.float64,
    )

    highs_arr = np.asarray(
        highs,
        dtype=np.float64,
    )

    low_max = (
        lows_arr.max()
        or 1.0
    )

    high_max = (
        highs_arr.max()
        or 1.0
    )

    return (
        (
            lows_arr
            / low_max
        ).tolist(),
        (
            highs_arr
            / high_max
        ).tolist(),
    )


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze_file(filepath: str) -> dict:
    """Analyze a single audio file.

    Returns a dict compatible with the existing Track fields, with the
    additional `loudness` field containing an approximate integrated LUFS
    measurement.
    """
    tags = _read_tags(
        filepath
    )

    y, full_duration = _decode_audio(
        filepath
    )

    # ------------------------------------------------------------------
    # Tempo
    # ------------------------------------------------------------------

    # Main STFT used by the rest of the analysis.
    mag = _stft_magnitude(
        y,
        FRAME_SIZE,
        HOP_SIZE,
    )

    # Dedicated beat-resolution STFT.
    tempo_mag = _stft_magnitude(
        y,
        TEMPO_FRAME_SIZE,
        TEMPO_HOP_SIZE,
    )

    # Kick: fundamental + low harmonics.
    # This is the primary BPM signal.
    kick_onset = _tempo_onset_envelope(
        tempo_mag,
        ANALYSIS_SR,
        TEMPO_FRAME_SIZE,
        40.0,
        160.0,
    )

    # Snare/clap: higher-frequency transient information.
    # Useful for confirming the beat and distinguishing subdivisions.
    snare_onset = _tempo_onset_envelope(
        tempo_mag,
        ANALYSIS_SR,
        TEMPO_FRAME_SIZE,
        150.0,
        4000.0,
    )

    # Kick dominates the BPM estimate.
    beat_onset = _combine_beat_envelopes(
        kick_onset,
        snare_onset,
    )

    tempo = _estimate_tempo(
        beat_onset,
        ANALYSIS_SR,
        TEMPO_HOP_SIZE,
    )

    tempo = float(
        np.clip(
            tempo,
            MIN_BPM,
            MAX_BPM,
        )
    )

    # General onset envelope is still used for genre/rhythmic features.
    onset_env = _onset_envelope(
        mag
    )

    # ------------------------------------------------------------------
    # Key
    # ------------------------------------------------------------------

    key_mag = _stft_magnitude(
        y,
        KEY_FRAME_SIZE,
        KEY_HOP_SIZE,
    )

    key_name = _detect_key_from_stft(
        key_mag,
        ANALYSIS_SR,
        KEY_FRAME_SIZE,
    )

    camelot = key_to_camelot(
        key_name
    )

    # ------------------------------------------------------------------
    # Loudness
    # ------------------------------------------------------------------

    loudness = _estimate_loudness(
        y,
        ANALYSIS_SR,
    )

    # ------------------------------------------------------------------
    # Spectral features
    # ------------------------------------------------------------------

    spectral = _spectral_features(
        mag,
        ANALYSIS_SR,
        FRAME_SIZE,
    )

    rhythmic = _rhythmic_features(
        onset_env
    )

    zcr = _zero_crossing_rate(
        y
    )

    # ------------------------------------------------------------------
    # Energy
    # ------------------------------------------------------------------

    rms = float(
        np.sqrt(
            np.mean(
                np.square(
                    y.astype(
                        np.float64
                    )
                )
            )
        )
        if len(y)
        else 0.0
    )

    centroid = spectral[
        "spectral_centroid"
    ]

    onset_density = rhythmic[
        "onset_density"
    ]

    # Keep energy_raw compatible with the existing normalization system,
    # but make it substantially more musically meaningful.
    rms_component = np.clip(
        rms * 5.0,
        0.0,
        5.0,
    )

    spectral_component = np.clip(
        centroid / 5000.0,
        0.0,
        1.5,
    )

    onset_component = np.clip(
        onset_density * 2.0,
        0.0,
        1.5,
    )

    loudness_component = np.clip(
        (
            loudness + 30.0
        ) / 30.0,
        0.0,
        1.5,
    )

    energy_raw = (
        0.40 * rms_component
        + 0.20 * spectral_component
        + 0.20 * onset_component
        + 0.20 * loudness_component
    )

    # ------------------------------------------------------------------
    # Genre
    # ------------------------------------------------------------------

    genre_features = dict(
        spectral
    )

    genre_features.update(
        rhythmic
    )

    genre_features[
        "zero_crossing_rate"
    ] = zcr

    genre_features[
        "tempo"
    ] = tempo

    genre_features[
        "loudness"
    ] = loudness

    # Preserve _estimate_genre(tags, tempo)'s signature while giving it
    # substantially richer information.
    genre_tags = dict(
        tags
    )

    genre_tags[
        "_genre_features"
    ] = genre_features

    genre = _estimate_genre(
        genre_tags,
        tempo,
    )

    # ------------------------------------------------------------------
    # Waveform and cover
    # ------------------------------------------------------------------

    waveform_low, waveform_high = (
        _compute_waveform_bands(
            y,
            ANALYSIS_SR,
        )
    )

    cover_path = _extract_cover(
        filepath
    )

    st = os.stat(
        filepath
    )

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    return {
        "filepath": filepath,
        "title": tags["title"],
        "artist": tags["artist"],
        "album": tags["album"],
        "genre": genre,
        "duration": float(
            full_duration
        ),
        "tempo": round(
            tempo,
            1,
        ),
        "key_name": key_name,
        "camelot": camelot,

        # New song parameter.
        # Approximate integrated loudness in LUFS.
        "loudness": round(
            loudness,
            1,
        ),

        "energy_raw": float(
            energy_raw
        ),
        "energy": 0.0,

        "cover_path": cover_path,

        "waveform_low": waveform_low,
        "waveform_high": waveform_high,

        "filesize": st.st_size,
        "mtime": st.st_mtime,
    }


# ---------------------------------------------------------------------------
# Full-resolution waveform
# ---------------------------------------------------------------------------

def compute_waveform_peaks(
    filepath: str,
    num_points: int = 600,
) -> list:
    """Decode the full file and downsample to peak amplitudes."""
    import miniaudio

    decoded = miniaudio.decode_file(
        filepath,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
        sample_rate=ANALYSIS_SR,
    )

    samples = np.frombuffer(
        bytes(decoded.samples),
        dtype=np.float32,
    )

    if len(samples) == 0:
        return [
            0.0
        ] * num_points

    peak = float(
        np.abs(
            samples
        ).max()
    ) or 1.0

    chunk = max(
        1,
        len(samples)
        // num_points,
    )

    peaks = []

    for i in range(
        num_points
    ):
        start = (
            i * chunk
        )

        seg = samples[
            start:
            start + chunk
        ]

        peaks.append(
            float(
                np.abs(seg).max()
            )
            / peak
            if len(seg)
            else 0.0
        )

    return peaks