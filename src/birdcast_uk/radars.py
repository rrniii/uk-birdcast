"""Radar metadata for BirdCast UK artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import csv
import json
from urllib.request import urlopen


@dataclass(frozen=True)
class BirdcastRadar:
    slug: str
    radar_num: str
    label: str
    latitude: float | None = None
    longitude: float | None = None
    height_m: float | None = None
    max_range_m: float | None = None
    range_source: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_RADARS: tuple[BirdcastRadar, ...] = tuple(
    BirdcastRadar(slug, radar_num, label)
    for slug, radar_num, label in (
        ("castor-bay", "07", "Castor Bay"),
        ("chenies", "05", "Chenies"),
        ("clee-hill", "03", "Clee Hill"),
        ("cobbacombe", "16", "Cobbacombe"),
        ("crug-y-gorrllwyn", "10", "Crug-y-Gorrllwyn"),
        ("deanhill", "21", "Dean Hill"),
        ("druima-starraig", "15", "Druima Starraig"),
        ("dudwick", "14", "Dudwick"),
        ("hameldon-hill", "04", "Hameldon Hill"),
        ("high-moorsley", "23", "High Moorsley"),
        ("holehead", "18", "Holehead"),
        ("ingham", "09", "Ingham"),
        ("jersey", "12", "Jersey"),
        ("munduff-hill", "19", "Munduff Hill"),
        ("predannack", "08", "Predannack"),
        ("thurnham", "20", "Thurnham"),
        ("wardon-hill", "11", "Wardon Hill"),
    )
)


def radar_records(radars: tuple[BirdcastRadar, ...] = DEFAULT_RADARS) -> list[dict[str, object]]:
    return [radar.to_dict() for radar in radars]


def load_radars(path: Path | None = None) -> list[BirdcastRadar]:
    if path is None:
        return list(DEFAULT_RADARS)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("radars", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("radar JSON must be a list or an object with a radars list")
        return [_radar_from_mapping(row) for row in rows]
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [_radar_from_mapping(row) for row in csv.DictReader(handle)]


def _radar_from_mapping(row: object) -> BirdcastRadar:
    if not isinstance(row, dict):
        raise ValueError("radar rows must be objects")
    return BirdcastRadar(
        slug=str(row.get("slug") or row.get("radar") or ""),
        radar_num=str(row.get("radar_num") or row.get("num") or ""),
        label=str(row.get("label") or row.get("name") or row.get("slug") or ""),
        latitude=_optional_float(row.get("latitude") or row.get("lat")),
        longitude=_optional_float(row.get("longitude") or row.get("lon")),
        height_m=_optional_float(row.get("height_m") or row.get("height")),
        max_range_m=_optional_float(row.get("max_range_m") or row.get("range_m")),
        range_source=str(row.get("range_source") or "") or None,
    )


def _optional_float(value: object) -> float | None:
    if value in ("", None):
        return None
    return float(value)  # type: ignore[arg-type]


def load_pvol_catalog(source: str | Path) -> dict[str, object]:
    source_text = str(source)
    if source_text.startswith(("http://", "https://")):
        with urlopen(source_text, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def radars_from_pvol_catalog(
    source: str | Path,
    *,
    default_max_range_m: float | None = None,
) -> list[BirdcastRadar]:
    payload = load_pvol_catalog(source)
    rows = payload.get("radars", [])
    if not isinstance(rows, list):
        raise ValueError("PVOL catalog must contain a radars list")
    radars = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        spatial = row.get("spatial") if isinstance(row.get("spatial"), dict) else {}
        slug = str(row.get("radar") or row.get("slug") or "")
        if not slug:
            continue
        catalog_range = _optional_float(spatial.get("max_range_m"))
        max_range_m = catalog_range if catalog_range is not None else default_max_range_m
        radars.append(
            BirdcastRadar(
                slug=slug,
                radar_num=str(row.get("radar_num") or ""),
                label=str(row.get("label") or slug.replace("-", " ").title()),
                latitude=_optional_float(spatial.get("latitude")),
                longitude=_optional_float(spatial.get("longitude")),
                height_m=_optional_float(spatial.get("height_m")),
                max_range_m=max_range_m,
                range_source=(
                    "pvol_catalog_spatial"
                    if catalog_range is not None
                    else "validated_odim_lp_geometry"
                    if max_range_m is not None
                    else None
                ),
            )
        )
    return sorted(radars, key=lambda radar: radar.slug)


def write_radars(path: Path, radars: list[BirdcastRadar], *, source: str = "") -> dict[str, object]:
    from .static_artifacts import utc_now, write_json

    payload = {
        "generated_at_utc": utc_now(),
        "source": source,
        "radars": [radar.to_dict() for radar in radars],
    }
    write_json(path, payload)
    return payload
