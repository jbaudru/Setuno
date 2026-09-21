"""Musical key <-> Camelot wheel mapping and harmonic-mixing compatibility."""

from typing import Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Camelot wheel
# ---------------------------------------------------------------------------

# Camelot code -> canonical musical key name
CAMELOT_TO_KEY = {
    "1B": "B major",
    "2B": "F# major",
    "3B": "Db major",
    "4B": "Ab major",
    "5B": "Eb major",
    "6B": "Bb major",
    "7B": "F major",
    "8B": "C major",
    "9B": "G major",
    "10B": "D major",
    "11B": "A major",
    "12B": "E major",

    "1A": "Ab minor",
    "2A": "Eb minor",
    "3A": "Bb minor",
    "4A": "F minor",
    "5A": "C minor",
    "6A": "G minor",
    "7A": "D minor",
    "8A": "A minor",
    "9A": "E minor",
    "10A": "B minor",
    "11A": "F# minor",
    "12A": "Db minor",
}

KEY_TO_CAMELOT = {
    key: code for code, key in CAMELOT_TO_KEY.items()
}


# ---------------------------------------------------------------------------
# Note normalization
# ---------------------------------------------------------------------------

# Canonical spellings used by CAMELOT_TO_KEY.
#
# We normalize enharmonic spellings before looking up a key in the Camelot
# mapping. Natural notes are kept as-is.
ENHARMONIC = {
    "C#": "Db",
    "D#": "Eb",
    "G#": "Ab",
    "A#": "Bb",

    "Gb": "F#",
    "Cb": "B",
    "Fb": "E",

    # Double spelling variants that may appear in imported metadata.
    "B#": "C",
    "E#": "F",
}


def normalize_note(root: str) -> str:
    """Normalize a musical note to the canonical spelling used by the wheel."""

    if not root:
        return ""

    root = root.strip()

    if not root:
        return ""

    # Standardize accidental symbols that may come from metadata.
    root = (
        root.replace("♯", "#")
            .replace("♭", "b")
    )

    # Normalize casing while preserving accidentals.
    root = root[0].upper() + root[1:]

    # Convert common enharmonic spellings.
    root = ENHARMONIC.get(root, root)

    return root


def normalize_mode(mode: str) -> str:
    """Normalize a mode string to either 'major' or 'minor'."""

    if not mode:
        return ""

    mode = mode.strip().lower()

    if mode in {
        "major",
        "maj",
        "mjr",
        "ionian",
    }:
        return "major"

    if mode in {
        "minor",
        "min",
        "m",
        "mnr",
        "aeolian",
    }:
        return "minor"

    return mode


def normalize_key_name(root: str, mode: str) -> str:
    """
    Normalize a raw note/mode pair into canonical
    'X major' / 'X minor' form.
    """

    root = normalize_note(root)
    mode = normalize_mode(mode)

    if not root or mode not in {"major", "minor"}:
        return ""

    return f"{root} {mode}"


# ---------------------------------------------------------------------------
# Key parsing
# ---------------------------------------------------------------------------

def parse_key_name(key_name: str) -> Optional[Tuple[str, str]]:
    """
    Parse common key representations.

    Examples
    --------
    'C major' -> ('C', 'major')
    'C minor' -> ('C', 'minor')
    'Cmaj'   -> ('C', 'major')
    'Cmin'   -> ('C', 'minor')
    'Cm'     -> ('C', 'minor')
    'F#m'    -> ('F#', 'minor')
    'Dbmaj'  -> ('Db', 'major')
    """

    if not key_name:
        return None

    value = (
        key_name.strip()
        .replace("♯", "#")
        .replace("♭", "b")
    )

    if not value:
        return None

    parts = value.split()

    # Explicit form: "C major", "F# minor", etc.
    if len(parts) >= 2:
        root = parts[0]
        mode = normalize_mode(" ".join(parts[1:]))

        if mode in {"major", "minor"}:
            return normalize_note(root), mode

    # Compact forms: Cmaj, Cmin, Cm, F#m, Dbmajor, etc.
    compact = value.replace(" ", "")

    suffixes = [
        ("major", "major"),
        ("minor", "minor"),
        ("maj", "major"),
        ("min", "minor"),
    ]

    lower = compact.lower()

    for suffix, mode in suffixes:
        if lower.endswith(suffix):
            root = compact[:-len(suffix)]
            root = normalize_note(root)

            if root:
                return root, mode

    # "Cm" / "C#m" / "Dbm"
    if len(compact) >= 2 and compact[-1].lower() == "m":
        root = normalize_note(compact[:-1])

        if root:
            return root, "minor"

    # If only a root was supplied, assume major.
    root = normalize_note(compact)

    if root:
        return root, "major"

    return None


def normalize_key_string(key_name: str) -> str:
    """
    Normalize a complete key string.

    Examples
    --------
    'Cmaj' -> 'C major'
    'Cm'   -> 'C minor'
    'C#min' -> 'Db minor'
    """

    parsed = parse_key_name(key_name)

    if not parsed:
        return ""

    root, mode = parsed
    return normalize_key_name(root, mode)


# ---------------------------------------------------------------------------
# Key <-> Camelot conversion
# ---------------------------------------------------------------------------

def key_to_camelot(key_name: str) -> str:
    """
    Convert a musical key name to its Camelot code.

    Accepts both verbose and compact representations.
    """

    normalized = normalize_key_string(key_name)

    if not normalized:
        return ""

    return KEY_TO_CAMELOT.get(normalized, "")


def camelot_to_key(code: str) -> str:
    """Convert a Camelot code to a canonical musical key name."""

    if not code:
        return ""

    code = code.strip().upper()

    return CAMELOT_TO_KEY.get(code, "")


def is_valid_camelot(code: str) -> bool:
    """Return True if `code` is a valid Camelot wheel code."""

    if not code:
        return False

    return code.strip().upper() in CAMELOT_TO_KEY


# ---------------------------------------------------------------------------
# Camelot wheel helpers
# ---------------------------------------------------------------------------

def _parse_camelot(code: str) -> Optional[Tuple[int, str]]:
    """
    Parse a Camelot code into (number, mode).

    Example:
        '8A' -> (8, 'A')
        '12B' -> (12, 'B')
    """

    if not code:
        return None

    code = code.strip().upper()

    if len(code) < 2:
        return None

    number_part = code[:-1]
    mode = code[-1]

    if mode not in {"A", "B"}:
        return None

    try:
        number = int(number_part)
    except ValueError:
        return None

    if number < 1 or number > 12:
        return None

    return number, mode


def camelot_neighbors(code: str) -> Set[str]:
    """
    Return the Camelot codes directly compatible with `code`.

    Compatible transitions are:

    - Same key
    - One step clockwise
    - One step counter-clockwise
    - Relative major/minor at the same wheel position
    """

    parsed = _parse_camelot(code)

    if not parsed:
        return set()

    number, mode = parsed

    clockwise = (number % 12) + 1
    counter_clockwise = ((number - 2) % 12) + 1
    other_mode = "A" if mode == "B" else "B"

    return {
        f"{number}{mode}",
        f"{clockwise}{mode}",
        f"{counter_clockwise}{mode}",
        f"{number}{other_mode}",
    }


def camelot_ring_distance(code_a: str, code_b: str) -> int:
    """
    Return the shortest distance between two positions on the same Camelot
    ring.

    Examples:
        8A -> 8A  = 0
        8A -> 9A  = 1
        8A -> 7A  = 1
        8A -> 10A = 2

    For different rings (A/B), the mode change itself adds one step.
    """

    parsed_a = _parse_camelot(code_a)
    parsed_b = _parse_camelot(code_b)

    if not parsed_a or not parsed_b:
        return 99

    number_a, mode_a = parsed_a
    number_b, mode_b = parsed_b

    circular_distance = abs(number_a - number_b)
    circular_distance = min(
        circular_distance,
        12 - circular_distance,
    )

    mode_distance = 0 if mode_a == mode_b else 1

    return circular_distance + mode_distance


def camelot_distance(code_a: str, code_b: str) -> int:
    """
    Harmonic compatibility distance.

    Returns:

        0 = identical Camelot position
        1 = directly compatible transition
        2 = nearby but not directly compatible
        3 = more distant / likely clash
        99 = invalid or unknown code

    This preserves the original 0/1/3 behavior for the important cases while
    providing more useful information for intermediate distances.
    """

    parsed_a = _parse_camelot(code_a)
    parsed_b = _parse_camelot(code_b)

    if not parsed_a or not parsed_b:
        return 99

    code_a = code_a.strip().upper()
    code_b = code_b.strip().upper()

    if code_a == code_b:
        return 0

    if code_b in camelot_neighbors(code_a):
        return 1

    ring_distance = camelot_ring_distance(code_a, code_b)

    if ring_distance == 2:
        return 2

    return 3


def keys_compatible(
    key_a: str,
    key_b: str,
    max_distance: int = 1,
) -> bool:
    """
    Return True if two musical keys are harmonically compatible.

    Parameters
    ----------
    key_a, key_b:
        Musical key names such as 'C major', 'Am', or 'F# minor'.

    max_distance:
        Maximum Camelot distance considered compatible.
        The default of 1 corresponds to standard Camelot-compatible moves.
    """

    camelot_a = key_to_camelot(key_a)
    camelot_b = key_to_camelot(key_b)

    if not camelot_a or not camelot_b:
        return False

    return camelot_distance(camelot_a, camelot_b) <= max_distance


def camelot_compatible(
    code_a: str,
    code_b: str,
) -> bool:
    """Return True if two Camelot codes are directly harmonically compatible."""

    return camelot_distance(code_a, code_b) <= 1


# ---------------------------------------------------------------------------
# Useful transition scoring
# ---------------------------------------------------------------------------

def harmonic_transition_score(
    code_a: str,
    code_b: str,
) -> float:
    """
    Return a continuous penalty useful for playlist optimization.

    Lower is better:

        0.0 = same key
        0.25 = directly compatible Camelot move
        1.0 = nearby key
        3.0 = harmonic clash
        10.0 = invalid/unknown key
    """

    distance = camelot_distance(code_a, code_b)

    if distance == 0:
        return 0.0

    if distance == 1:
        return 0.25

    if distance == 2:
        return 1.0

    if distance == 3:
        return 3.0

    return 10.0