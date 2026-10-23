"""Data models shared across the application."""
import math
from dataclasses import dataclass, field
from typing import Optional, TypedDict


CUE_LABELS = "ABCDEFGH"
CUE_COLORS = (
    "#ff4050", "#ff9820", "#ffe040", "#45dc70",
    "#20d8df", "#488cff", "#b878ff", "#ff68c0",
)


class HotCue(TypedDict):
    slot: int
    time: float


def validate_hot_cues(cues: list[HotCue], duration: float = 0.0) -> list[HotCue]:
    result: list[HotCue] = []
    slots = set()
    for cue in cues:
        slot, time = cue["slot"], cue["time"]
        if type(slot) is not int or not 0 <= slot < 8 or slot in slots:
            raise ValueError("Hot cues must use unique slots A through H.")
        if not math.isfinite(time) or time < 0 or (duration > 0 and time > duration):
            raise ValueError("The cue position must be within the track.")
        slots.add(slot)
        result.append({"slot": slot, "time": float(time)})
    return sorted(result, key=lambda cue: cue["slot"])


@dataclass
class Track:
    id: Optional[int] = None
    filepath: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    duration: float = 0.0          # seconds
    tempo: float = 0.0             # BPM
    beat_offset: Optional[float] = None  # measured phase of the beat grid in seconds
    key_name: str = ""             # e.g. "A minor"
    camelot: str = ""              # e.g. "8A"
    energy_raw: float = 0.0
    energy: float = 0.0            # normalized 1-10
    loudness: float = 0.0
    filesize: int = 0
    mtime: float = 0.0
    added_at: str = ""
    tempo_manual: bool = False     # True once the user has manually corrected the BPM
    key_manual: bool = False       # True once the user has manually corrected the key
    favorite: bool = False         # True once the user has hearted the track
    cover_path: str = ""           # cached embedded album-art file, empty if none
    waveform_low: list = field(default_factory=list)   # bass-band envelope, computed at scan time
    waveform_high: list = field(default_factory=list)  # treble-band envelope, computed at scan time
    waveform_peaks: list = field(default_factory=list)  # full-track envelope shared by mini/full waveform views
    waveform_bands: list = field(default_factory=list)  # per-frame bass/mid/high spectral magnitudes
    hot_cues: list[HotCue] = field(default_factory=list)
    spectral_features: dict = field(default_factory=dict)  # analyzed spectral descriptors for similarity matching
    rhythmic_features: dict = field(default_factory=dict)  # analyzed rhythmic descriptors for similarity matching

    @property
    def display_name(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.title or self.filepath

    @property
    def duration_str(self) -> str:
        m, s = divmod(int(self.duration), 60)
        return f"{m}:{s:02d}"
