from __future__ import annotations  # Deferred annotations

import json  # JSON data
from pathlib import Path  # File paths
from typing import Any, Iterator, Sequence  # Type hints

import geopandas as gpd  # Geospatial data
import requests  # HTTP requests


LAYER_URL = (
    "https://gisagodnr.state.mi.us/arcgis/rest/services/"
    "DNR/DNRTrailsOPENDATA/MapServer/2"
)
QUERY_URL = f"{LAYER_URL}/query"

WHERE_CLAUSE = "Peninsula = 'Upper Peninsula'"
BATCH_SIZE = 500

RAW_DIR = Path("data/raw")
REPORT_DIR = Path("reports")

OUTPUT_PATH = RAW_DIR / "dnr_up_hiking_trails.geojson"
PROFILE_PATH = REPORT_DIR / "dnr_up_hiking_trails_profile.json"

DOWNLOAD_FIELDS = [
    "OBJECTID",
    "DNRTrail",
    "TrailNamePrimary",
    "HikingName",
    "FacilityName",
    "County",
    "Peninsula",
    "Hiking",
    "TrailApprovalStatus",
    "TrailUseCategory",
    "OpenClosedStatusNonmotor",
    "SurfaceType",
    "TrailWidthFeet",
    "ADAAccessible",
    "SegmentLengthMiles",
    "SpecialRestrictionType",
    "TrailAdministrator",
    "RecreationSearchFacilityID",
    "RecreationSearchTrailID",
    "last_edited_date",
]


def request_json(params: dict[str, Any]) -> dict[str, Any]:
    """Request JSON from the ArcGIS service and validate the response."""

    try:
        response = requests.get(
            QUERY_URL,
            params=params,
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"DNR API request failed: {exc}") from exc

    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError("DNR API did not return valid JSON.") from exc

    if "error" in payload:
        error = payload["error"]
        message = error.get("message", "Unknown ArcGIS error")
        details = error.get("details", [])
        raise RuntimeError(f"{message}: {details}")

    return payload


def get_object_ids() -> list[int]:
    """Return all object IDs matching the UP filter."""

    payload = request_json(
        {
            "where": WHERE_CLAUSE,
            "returnIdsOnly": "true",
            "returnGeometry": "false",
            "f": "json",
        }
    )

    object_ids = payload.get("objectIds")

    if object_ids is None:
        raise RuntimeError("The API response did not contain an objectIds field.")

    if not object_ids:
        raise RuntimeError("The API returned zero matching trail segments.")

    return sorted(int(object_id) for object_id in object_ids)


def batched(
    values: Sequence[int],
    batch_size: int,
) -> Iterator[list[int]]:
    """Yield consecutive batches from a sequence."""

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero.")

    for start in range(0, len(values), batch_size):
        yield list(values[start : start + batch_size])


def download_batch(object_ids: list[int]) -> dict[str, Any]:
    """Download one batch of features as GeoJSON."""

    payload = request_json(
        {
            "objectIds": ",".join(map(str, object_ids)),
            "outFields": ",".join(DOWNLOAD_FIELDS),
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
    )

    features = payload.get("features")

    if features is None:
        raise RuntimeError("GeoJSON response did not contain a features field.")

    return payload


def download_all_features(
    object_ids: list[int],
) -> dict[str, Any]:
    """Download and combine every matching trail feature."""

    combined_features: list[dict[str, Any]] = []
    batches = list(batched(object_ids, BATCH_SIZE))

    print(f"Matching object IDs: {len(object_ids):,}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Number of batches: {len(batches)}")

    for batch_number, object_id_batch in enumerate(
        batches,
        start=1,
    ):
        print(
            f"Downloading batch {batch_number}/{len(batches)} "
            f"({len(object_id_batch)} records)..."
        )

        payload = download_batch(object_id_batch)
        features = payload["features"]

        combined_features.extend(features)

    return {
        "type": "FeatureCollection",
        "features": combined_features,
    }


def build_profile(
    trails: gpd.GeoDataFrame,
    expected_count: int,
) -> dict[str, Any]:
    """Create a validation and data-quality report."""

    missing_values = {
        column: int(trails[column].isna().sum())
        for column in trails.columns
        if column != trails.geometry.name
    }

    geometry_types = {
        str(geometry_type): int(count)
        for geometry_type, count in trails.geometry.geom_type.value_counts(
            dropna=False
        ).items()
    }

    distinct_values = {}

    categorical_fields = [
        "DNRTrail",
        "Peninsula",
        "Hiking",
        "TrailApprovalStatus",
        "OpenClosedStatusNonmotor",
        "TrailUseCategory",
        "SurfaceType",
        "ADAAccessible",
    ]

    for column in categorical_fields:
        if column in trails.columns:
            values = trails[column].dropna().astype(str).unique()
            distinct_values[column] = sorted(values.tolist())

    duplicate_object_ids = 0

    if "OBJECTID" in trails.columns:
        duplicate_object_ids = int(trails["OBJECTID"].duplicated().sum())

    return {
        "source": LAYER_URL,
        "where_clause": WHERE_CLAUSE,
        "expected_record_count": expected_count,
        "downloaded_record_count": len(trails),
        "counts_match": len(trails) == expected_count,
        "column_count": len(trails.columns),
        "columns": list(trails.columns),
        "crs": str(trails.crs),
        "geometry_types": geometry_types,
        "missing_geometry_count": int(trails.geometry.isna().sum()),
        "empty_geometry_count": int(trails.geometry.is_empty.sum()),
        "invalid_geometry_count": int((~trails.geometry.is_valid).sum()),
        "duplicate_object_id_count": duplicate_object_ids,
        "missing_values": missing_values,
        "distinct_filter_values": distinct_values,
    }


def validate_download(
    trails: gpd.GeoDataFrame,
    expected_object_ids: list[int],
) -> None:
    """Raise an error when critical download checks fail."""

    if trails.empty:
        raise RuntimeError("Downloaded GeoDataFrame is empty.")

    if "OBJECTID" not in trails.columns:
        raise RuntimeError("Downloaded data does not contain OBJECTID.")

    downloaded_ids = set(trails["OBJECTID"].dropna().astype(int))
    expected_ids = set(expected_object_ids)

    missing_ids = expected_ids - downloaded_ids
    unexpected_ids = downloaded_ids - expected_ids

    if missing_ids:
        preview = sorted(missing_ids)[:10]
        raise RuntimeError(
            f"{len(missing_ids)} object IDs were not downloaded. "
            f"First missing IDs: {preview}"
        )

    if unexpected_ids:
        preview = sorted(unexpected_ids)[:10]
        raise RuntimeError(
            f"{len(unexpected_ids)} unexpected object IDs appeared. "
            f"First unexpected IDs: {preview}"
        )

    duplicate_count = int(trails["OBJECTID"].duplicated().sum())

    if duplicate_count:
        raise RuntimeError(f"Found {duplicate_count} duplicate OBJECTID values.")

    if trails.geometry.isna().any():
        raise RuntimeError("One or more records have missing geometry.")

    if trails.geometry.is_empty.any():
        raise RuntimeError("One or more records have empty geometry.")

    peninsula_values = set(trails["Peninsula"].dropna().astype(str))

    if peninsula_values != {"Upper Peninsula"}:
        raise RuntimeError(
            "Unexpected Peninsula values found: " f"{sorted(peninsula_values)}"
        )


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("Requesting all matching object IDs...")
    object_ids = get_object_ids()

    feature_collection = download_all_features(object_ids)

    OUTPUT_PATH.write_text(
        json.dumps(feature_collection),
        encoding="utf-8",
    )

    print("\nLoading combined GeoJSON with GeoPandas...")
    trails = gpd.read_file(OUTPUT_PATH)

    validate_download(trails, object_ids)

    profile = build_profile(
        trails=trails,
        expected_count=len(object_ids),
    )

    PROFILE_PATH.write_text(
        json.dumps(profile, indent=2, default=str),
        encoding="utf-8",
    )

    print("\nFull download complete.")
    print(f"Expected records: {len(object_ids):,}")
    print(f"Downloaded records: {len(trails):,}")
    print(f"CRS: {trails.crs}")
    print("Geometry types:\n" f"{trails.geometry.geom_type.value_counts()}")
    print(f"GeoJSON saved to: {OUTPUT_PATH}")
    print(f"Profile saved to: {PROFILE_PATH}")


if __name__ == "__main__":
    main()
