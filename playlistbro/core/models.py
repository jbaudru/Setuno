"""Data models shared across the application."""
from dataclasses import dataclass, field
from typing import Optional


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
