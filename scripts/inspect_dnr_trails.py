from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import requests

LAYER_URL = (
    "https://gisagodnr.state.mi.us/arcgis/rest/services/"
    "DNR/DNRTrailsOPENDATA/MapServer/2"
)
QUERY_URL = f"{LAYER_URL}/query"

RAW_DIR = Path("data/raw")
REPORT_DIR = Path("reports")

SAMPLE_PATH = RAW_DIR / "dnr_hiking_trails_sample.geojson"
PROFILE_PATH = REPORT_DIR / "dnr_hiking_trails_sample_profile.json"

FILTER_FIELDS = [
    "Peninsula",
    "Hiking",
    "TrailApprovalStatus",
    "OpenClosedStatusNonmotor",
    "SurfaceType",
    "ADAAccessible",
]

DOWNLOAD_FIELDS = [
    "OBJECTID",
    "TrailNamePrimary",
    "HikingName",
    "FacilityName",
    "County",
    "Peninsula",
    "Hiking",
    "TrailApprovalStatus",
    "OpenClosedStatusNonmotor",
    "SurfaceType",
    "ADAAccessible",
    "SegmentLengthMiles",
    "SpecialRestrictionType",
    "TrailAdministrator",
    "RecreationSearchFacilityID",
    "RecreationSearchTrailID",
    "last_edited_date",
]


def request_json(params: dict[str, Any]) -> dict[str, Any]:
    """Send an ArcGIS query and validate the returned JSON."""

    try:
        response = requests.get(QUERY_URL, params=params, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"DNR API request failed: {exc}") from exc

    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError("DNR API did not return valid JSON.") from exc

    if "error" in payload:
        error = payload["error"]
        message = error.get("message", "Unknown ArcGIS API error")
        details = error.get("details", [])
        raise RuntimeError(f"{message}: {details}")

    return payload


def get_distinct_values(field: str) -> list[Any]:
    """Retrieve the values currently used by one categorical field."""

    payload = request_json(
        {
            "where": "1=1",
            "outFields": field,
            "returnDistinctValues": "true",
            "returnGeometry": "false",
            "orderByFields": field,
            "f": "json",
        }
    )

    values = {
        feature.get("attributes", {}).get(field)
        for feature in payload.get("features", [])
    }

    return sorted(
        (value for value in values if value is not None),
        key=lambda value: str(value).casefold(),
    )


def find_upper_peninsula_value(values: list[Any]) -> str:
    """Find the API's exact label for the Upper Peninsula."""

    for value in values:
        if str(value).strip().casefold() == "upper peninsula":
            return str(value)

    raise RuntimeError(
        "The value 'Upper Peninsula' was not found. "
        f"Available Peninsula values: {values}"
    )


def download_up_sample(up_value: str, record_count: int = 100) -> dict[str, Any]:
    """Download a limited GeoJSON sample of Upper Peninsula trail segments."""

    escaped_value = up_value.replace("'", "''")

    return request_json(
        {
            "where": f"Peninsula = '{escaped_value}'",
            "outFields": ",".join(DOWNLOAD_FIELDS),
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "OBJECTID",
            "resultRecordCount": record_count,
            "f": "geojson",
        }
    )


def build_profile(
    trails: gpd.GeoDataFrame,
    distinct_values: dict[str, list[Any]],
) -> dict[str, Any]:
    """Create a small JSON-serializable data quality profile."""

    missing_counts = {
        column: int(trails[column].isna().sum())
        for column in DOWNLOAD_FIELDS
        if column in trails.columns
    }

    geometry_types = {
        str(name): int(count)
        for name, count in trails.geometry.geom_type.value_counts(
            dropna=False
        ).items()
    }

    return {
        "source": LAYER_URL,
        "sample_row_count": len(trails),
        "column_count": len(trails.columns),
        "columns": list(trails.columns),
        "crs": str(trails.crs),
        "geometry_types": geometry_types,
        "missing_geometry_count": int(trails.geometry.isna().sum()),
        "invalid_geometry_count": int((~trails.geometry.is_valid).sum()),
        "missing_values": missing_counts,
        "distinct_filter_values": distinct_values,
    }


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("Inspecting categorical values...")
    distinct_values = {
        field: get_distinct_values(field)
        for field in FILTER_FIELDS
    }

    for field, values in distinct_values.items():
        print(f"\n{field}:")
        for value in values:
            print(f"  - {value}")

    up_value = find_upper_peninsula_value(
        distinct_values["Peninsula"]
    )

    print(f"\nDownloading sample using Peninsula = {up_value!r}...")
    geojson = download_up_sample(up_value)

    features = geojson.get("features", [])
    if not features:
        raise RuntimeError(
            "The API returned zero Upper Peninsula trail features."
        )

    SAMPLE_PATH.write_text(
        json.dumps(geojson, indent=2),
        encoding="utf-8",
    )

    trails = gpd.read_file(SAMPLE_PATH)

    if trails.empty:
        raise RuntimeError(
            "GeoPandas loaded the sample, but it contained no records."
        )

    profile = build_profile(trails, distinct_values)

    PROFILE_PATH.write_text(
        json.dumps(profile, indent=2, default=str),
        encoding="utf-8",
    )

    print("\nSample inspection complete.")
    print(f"Rows: {len(trails):,}")
    print(f"Columns: {len(trails.columns)}")
    print(f"CRS: {trails.crs}")
    print(f"Geometry types:\n{trails.geometry.geom_type.value_counts()}")
    print(f"Sample saved to: {SAMPLE_PATH}")
    print(f"Profile saved to: {PROFILE_PATH}")


if __name__ == "__main__":
    main()