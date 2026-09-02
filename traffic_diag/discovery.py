"""Filesystem discovery: enumerate years, locations, and study folders.

Sensitive to the on-disk layout so newly-added years/locations appear
automatically (used by both the CLI and the Streamlit dashboard).

Layout::

    <base>/<year>/<Location>_<YYYYMMDD>/...
    <base>/<year>/_Compromised Studies/<Location>_<YYYYMMDD>/...   (labeled bad)
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Subfolders inside a year that hold studies but are not themselves locations.
SPECIAL_SUBDIRS = {
    "_compromised studies": "compromised",
    "_incomplete": "incomplete",
}

_DATE_RE = re.compile(r"(\d{8})\d*$")   # trailing 8+ digit run (handles typo'd 9-digit dates)


@dataclass(frozen=True)
class Study:
    """A single radar deployment / study folder on disk."""

    path: str
    year: int
    location: str
    install_date: Optional[date]
    source_name: str = "radar"
    status: str = "normal"            # normal | compromised | incomplete

    @property
    def folder_name(self) -> str:
        return os.path.basename(self.path.rstrip("/\\"))

    @property
    def study_id(self) -> str:
        return self.folder_name

    @property
    def is_compromised(self) -> bool:
        return self.status == "compromised"

    def files(self, suffix_glob: str) -> list[str]:
        """All files in the folder matching ``*<suffix_glob>``, sorted."""
        return sorted(glob.glob(os.path.join(self.path, f"*{suffix_glob}")))

    def file(self, suffix_glob: str) -> Optional[str]:
        """First file in the folder matching ``*<suffix_glob>`` (e.g. '_Notes.txt')."""
        hits = self.files(suffix_glob)
        return hits[0] if hits else None

    @property
    def report_xlsx(self) -> Optional[str]:
        return self.file("_Report.xlsx")

    @property
    def notes_path(self) -> Optional[str]:
        return self.file("_Notes.txt")

    @property
    def loc_photos(self) -> list[str]:
        """All installation site photos, sorted. Most sites have one (``*_Loc.*``),
        but some have several named ``*_Loc1.*`` / ``*_Loc2.*``; return them all so
        callers can show every photo, not just the first."""
        return self.files("_Loc*.*") or self.files("_loc*.*")

    @property
    def loc_photo(self) -> Optional[str]:
        """The primary installation site photo (first of ``loc_photos``), or None."""
        photos = self.loc_photos
        return photos[0] if photos else None

    @property
    def map_image(self) -> Optional[str]:
        """The location map (``*_Map.*``, also ``*_Map1.*`` etc.)."""
        return self.file("_Map*.*") or self.file("_map*.*")

    @property
    def loc_gps(self) -> Optional[tuple]:
        """(lat, lon) decimal degrees from the installation photos' EXIF GPS, or None.

        When a site has several photos, use the first one carrying a USABLE fix —
        in a multi-photo set not every image is geotagged, and some carry a
        placeholder 0/0 "no fix", which ``photo_gps`` rejects. So a site whose
        first photo has no lock still gets mapped from a later one.
        """
        for p in self.loc_photos:
            gps = photo_gps(p)
            if gps:
                return gps
        return None

    @property
    def loc_maps_url(self) -> Optional[str]:
        """A Google Maps link to the installation photo's GPS location, or None."""
        gps = self.loc_gps
        return maps_url(*gps) if gps else None

    @property
    def study_type(self) -> str:
        """'regular' (routine annual count) or 'ad-hoc' — inferred from the notes."""
        return classify_study_type(read_notes(self).get("raw", ""))


def _dms_to_decimal(dms, ref) -> float:
    """(deg, min, sec) + hemisphere ref -> signed decimal degrees."""
    d, m, s = (float(x) for x in dms)
    val = d + m / 60.0 + s / 3600.0
    return -val if str(ref).upper() in ("S", "W") else val


def is_usable_gps(lat, lon) -> bool:
    """True for coordinates that describe a real place.

    A camera that never got a satellite lock writes latitude and longitude of
    exactly zero rather than omitting the tags, and 0,0 is a point in the Gulf of
    Guinea - so mapping it produces a confident pin in the Atlantic. 106 of the
    archive's installation photos carry such a fix. Out-of-range values are
    rejected on the same grounds: they cannot be a location either.
    """
    if lat is None or lon is None:
        return False
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if lat == 0 and lon == 0:
        return False
    return abs(lat) <= 90 and abs(lon) <= 180


def photo_gps(path: Optional[str]) -> Optional[tuple]:
    """(lat, lon) decimal degrees from an image's EXIF GPS tags, or None.

    Returns None when the file is missing, has no GPS block, can't be read, or
    carries a placeholder 0/0 "no fix" (see ``is_usable_gps``). Callers can treat
    a non-None result as a mappable location.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        from PIL import ExifTags, Image
        with Image.open(path) as img:
            gps = img.getexif().get_ifd(ExifTags.IFD.GPSInfo)
        if not gps:
            return None
        t = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}
        lat, lat_ref = t.get("GPSLatitude"), t.get("GPSLatitudeRef")
        lon, lon_ref = t.get("GPSLongitude"), t.get("GPSLongitudeRef")
        if not (lat and lon and lat_ref and lon_ref):
            return None
        lat_d = round(_dms_to_decimal(lat, lat_ref), 6)
        lon_d = round(_dms_to_decimal(lon, lon_ref), 6)
        if not is_usable_gps(lat_d, lon_d):
            return None
        return (lat_d, lon_d)
    except Exception:
        return None


def maps_url(lat: float, lon: float) -> str:
    """Google Maps link that drops a pin at (lat, lon)."""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def parse_folder_name(name: str) -> tuple[str, Optional[date]]:
    """Split ``<Location>_<YYYYMMDD>`` into (location, date). Date may be None."""
    m = _DATE_RE.search(name)
    if not m:
        return name, None
    digits = m.group(1)
    location = name[: m.start()].rstrip("_")
    try:
        d = date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        d = None
    return (location or name), d


def find_years(base: str) -> list[int]:
    """Return sorted 4-digit year folders present under ``base``."""
    years = []
    for entry in os.scandir(base):
        if entry.is_dir() and re.fullmatch(r"\d{4}", entry.name):
            years.append(int(entry.name))
    return sorted(years)


def _is_study_dir(path: str) -> bool:
    """A study folder contains a raw CSV or a report workbook."""
    return bool(
        glob.glob(os.path.join(path, "*_Raw.csv"))
        or glob.glob(os.path.join(path, "*_Report.xlsx"))
    )


def find_studies(
    base: str,
    year: Optional[int] = None,
    include_compromised: bool = True,
    include_incomplete: bool = True,
    source_name: str = "radar",
) -> list[Study]:
    """Discover all study folders, optionally filtered to one year."""
    years = [year] if year is not None else find_years(base)
    studies: list[Study] = []
    for yr in years:
        ydir = os.path.join(base, str(yr))
        if not os.path.isdir(ydir):
            continue
        for entry in os.scandir(ydir):
            if not entry.is_dir():
                continue
            special = SPECIAL_SUBDIRS.get(entry.name.lower())
            if special:
                if special == "compromised" and not include_compromised:
                    continue
                if special == "incomplete" and not include_incomplete:
                    continue
                for sub in os.scandir(entry.path):
                    if sub.is_dir() and _is_study_dir(sub.path):
                        studies.append(_make_study(sub.path, yr, special, source_name))
            elif _is_study_dir(entry.path):
                studies.append(_make_study(entry.path, yr, "normal", source_name))
    studies.sort(key=lambda s: (s.year, s.location, s.install_date or date.min))
    return studies


def _make_study(path: str, year: int, status: str, source_name: str) -> Study:
    location, d = parse_folder_name(os.path.basename(path))
    return Study(path=path, year=year, location=location, install_date=d,
                 source_name=source_name, status=status)


def find_locations(base: str, year: int, **kwargs) -> list[Study]:
    """Studies for one year (dashboard populates its location dropdown from this)."""
    return find_studies(base, year=year, **kwargs)


# A deliberate "Limit: 30" / "Speed Limit: 30" field (colon required) — does NOT
# match prose like "construction speed limit 25 mph" or "...limit sign from 35 MPH".
_LIMIT_RE = re.compile(r"(?i)\blimit\s*:\s*(\d{1,2})\b")

# Study-type markers in the notes. Only two explicit markers are trusted:
# "Annual Count" (the routine recurring program) and "Ad-hoc" (an explicit one-off,
# however spaced/hyphenated). Notes carrying neither marker are left unclassified
# (empty) rather than guessed at — a blank field the user can review/fill by hand.
_ADHOC_RE = re.compile(r"(?i)ad[\s\-]?hoc")
_ANNUAL_RE = re.compile(r"(?i)annual\s+count")


# Field names as they actually appear in the archive, mapped to the field they
# mean. Matching only the correct spelling cost eleven studies their direction
# label outright: a "Outgoung: SB" line fell through to ``flags``, ``outgoing``
# stayed None, and every report for that study fell back to the generic word
# "Outgoing" instead of "SB". The typos are real and long-standing, so the parser
# recognises them rather than requiring the source files to be corrected first.
#
# "Request:" is the older label for "Source:" - both carry "Annual Count",
# "Corridor Study" and the like - so it reads as the source field.
#
# Keys are matched exactly (lower-cased, colon stripped), never fuzzily: a line
# like "On South side Incoming: ..." or "Notes after retreival:" is prose and must
# stay in ``flags``, which an edit-distance match would get wrong.
FIELD_ALIASES = {
    "incoming": "incoming",
    "icoming": "incoming",
    "outgoing": "outgoing",
    "outgoung": "outgoing",
    "outoging": "outgoing",
    "outgonig": "outgoing",
    "outgoingp": "outgoing",
    "source": "source",
    "soure": "source",
    "request": "source",
}


def field_for(line: str) -> Optional[str]:
    """Which notes field a line sets ('incoming' | 'outgoing' | 'source'), or None.

    Only the text before the first colon is considered, so free prose that happens
    to contain a colon is left alone.
    """
    head, sep, _ = line.partition(":")
    if not sep:
        return None
    return FIELD_ALIASES.get(head.strip().lower())


def classify_study_type(text: str) -> str:
    """Classify a study from its notes text. Returns one of:

    - ``'regular'`` — the routine annual-count program (notes mention "Annual Count").
    - ``'ad-hoc'``  — an explicit "Ad-hoc" source line.
    - ``''`` (empty) — no explicit marker, or empty/unreadable notes. Left blank on
      purpose so unlabeled studies aren't silently lumped into a category.

    An explicit "Ad-hoc" marker wins over "Annual Count" if a note somehow has both.
    """
    if not text:
        return ""
    if _ADHOC_RE.search(text):
        return "ad-hoc"
    if _ANNUAL_RE.search(text):
        return "regular"
    return ""


def read_notes(study: Study) -> dict:
    """Parse ``*_Notes.txt`` into {incoming, outgoing, source, limit, flags, raw}."""
    info: dict = {"incoming": None, "outgoing": None, "source": None,
                  "limit": None, "flags": [], "raw": ""}
    path = study.notes_path
    if not path or not os.path.exists(path):
        return info
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    info["raw"] = text.strip()
    m = _LIMIT_RE.search(text)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 70:
            info["limit"] = v
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        field = field_for(line)
        if field:
            info[field] = line.split(":", 1)[1].strip()
        else:
            info["flags"].append(line)
    return info
