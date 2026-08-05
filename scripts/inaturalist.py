"""Reusable iNaturalist data functions for What's UP Outdoors."""

from datetime import date, timedelta
from pathlib import Path
import time

import geopandas as gpd
import pandas as pd
import requests


API_URL = "https://api.inaturalist.org/v1/observations"
PROJECTED_CRS = "EPSG:3078"
MILES_TO_METERS = 1609.344

TAXON_GROUPS = {
    "Birds": "Aves",
    "Mammals": "Mammalia",
    "Plants": "Plantae",
    "Fungi": "Fungi",
    "Reptiles": "Reptilia",
    "Insects": "Insecta",
}

OBSERVATION_COLUMNS = [
    "observation_id",
    "observed_on",
    "common_name",
    "scientific_name",
    "iconic_taxon",
    "thumbnail_url",
    "longitude",
    "latitude",
    "positional_accuracy",
]


def load_trails(path: str | Path) -> gpd.GeoDataFrame:
    """Load grouped trails and validate the required fields."""
    trails = gpd.read_parquet(path)

    missing = {"TrailGroupName", "geometry"} - set(trails.columns)
    if missing:
        raise ValueError(f"Missing trail columns: {sorted(missing)}")

    if trails.crs is None:
        raise ValueError("Trail data does not have a CRS.")

    return trails


def load_historical_observations(
    path: str | Path,
    start_year: int = 2015,
    end_year: int = 2026,
    months: tuple[int, ...] = (9, 10),
    max_accuracy_meters: float = MILES_TO_METERS,
) -> pd.DataFrame:
    """Load and clean the historical iNaturalist CSV export."""
    columns = [
        "id",
        "observed_on",
        "quality_grade",
        "image_url",
        "latitude",
        "longitude",
        "public_positional_accuracy",
        "coordinates_obscured",
        "common_name",
        "iconic_taxon_name",
        "taxon_species_name",
    ]

    observations = pd.read_csv(path, usecols=columns, low_memory=False)

    observations["observed_on"] = pd.to_datetime(
        observations["observed_on"],
        errors="coerce",
    )

    for column in ["latitude", "longitude", "public_positional_accuracy"]:
        observations[column] = pd.to_numeric(
            observations[column],
            errors="coerce",
        )

    obscured = (
        observations["coordinates_obscured"]
        .astype(str)
        .str.strip()
        .str.upper()
        .eq("TRUE")
    )

    observations = observations.loc[
        observations["quality_grade"].isin(["research", "needs_id"])
        & observations["observed_on"].dt.year.between(start_year, end_year)
        & observations["observed_on"].dt.month.isin(months)
        & observations["latitude"].notna()
        & observations["longitude"].notna()
        & observations["taxon_species_name"].notna()
        & observations["iconic_taxon_name"].isin(TAXON_GROUPS.values())
        & ~obscured
        & (
            observations["public_positional_accuracy"].isna()
            | (
                observations["public_positional_accuracy"]
                <= max_accuracy_meters
            )
        )
    ].copy()

    observations = observations.rename(
        columns={
            "id": "observation_id",
            "image_url": "thumbnail_url",
            "public_positional_accuracy": "positional_accuracy",
            "iconic_taxon_name": "iconic_taxon",
            "taxon_species_name": "scientific_name",
        }
    )

    return observations[OBSERVATION_COLUMNS]


def create_trail_buffer(
    trails: gpd.GeoDataFrame,
    trail_name: str,
    buffer_miles: float = 1.0,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Select one trail and buffer its full geometry."""
    selected = trails.loc[
        trails["TrailGroupName"] == trail_name
    ].copy()

    if len(selected) != 1:
        raise ValueError(
            f"Expected one trail named {trail_name!r}; "
            f"found {len(selected)}."
        )

    projected = selected.to_crs(PROJECTED_CRS)
    buffer_meters = buffer_miles * MILES_TO_METERS

    trail_buffer = gpd.GeoDataFrame(
        projected[["TrailGroupName"]].copy(),
        geometry=projected.geometry.buffer(buffer_meters),
        crs=PROJECTED_CRS,
    )

    return selected, trail_buffer


def filter_to_buffer(
    observations: pd.DataFrame,
    trail_buffer: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Keep observations inside the exact trail buffer."""
    if observations.empty:
        empty = observations.copy()
        empty["geometry"] = None
        return gpd.GeoDataFrame(
            empty,
            geometry="geometry",
            crs=PROJECTED_CRS,
        )

    points = gpd.GeoDataFrame(
        observations.copy(),
        geometry=gpd.points_from_xy(
            observations["longitude"],
            observations["latitude"],
        ),
        crs="EPSG:4326",
    ).to_crs(PROJECTED_CRS)

    buffer_geometry = trail_buffer.geometry.iloc[0]

    return points.loc[
        points.geometry.within(buffer_geometry)
    ].copy()


def fetch_recent_observations(
    trail_buffer: gpd.GeoDataFrame,
    days: int = 14,
    max_pages: int = 5,
    timeout: int = 30,
    max_accuracy_meters: float = MILES_TO_METERS,
) -> tuple[pd.DataFrame, dict]:
    """Fetch recent species-level observations near a trail."""
    if days < 1 or max_pages < 1:
        raise ValueError("days and max_pages must be at least 1.")

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    west, south, east, north = (
        trail_buffer.to_crs("EPSG:4326").total_bounds
    )

    params = {
        "swlat": south,
        "swlng": west,
        "nelat": north,
        "nelng": east,
        "d1": start_date.isoformat(),
        "d2": end_date.isoformat(),
        "verifiable": "true",
        "mappable": "true",
        "iconic_taxa[]": list(TAXON_GROUPS.values()),
        "per_page": 200,
        "order_by": "observed_on",
        "order": "desc",
    }
    headers = {"User-Agent": "Whats-UP-Outdoors/0.1"}

    results = []
    total_results = 0

    for page in range(1, max_pages + 1):
        params["page"] = page

        response = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()
        page_results = data.get("results", [])
        total_results = data.get("total_results", 0)
        results.extend(page_results)

        if len(results) >= total_results or not page_results:
            break

        time.sleep(1)

    rows = []

    for observation in results:
        taxon = observation.get("taxon") or {}
        geojson = observation.get("geojson") or {}
        photos = observation.get("photos") or []
        coordinates = geojson.get("coordinates") or [None, None]
        accuracy = observation.get("positional_accuracy")

        valid_coordinates = (
            len(coordinates) >= 2
            and coordinates[0] is not None
            and coordinates[1] is not None
        )

        if (
            taxon.get("rank") != "species"
            or not valid_coordinates
            or observation.get("coordinates_obscured", False)
            or (
                accuracy is not None
                and accuracy > max_accuracy_meters
            )
        ):
            continue

        rows.append(
            {
                "observation_id": observation.get("id"),
                "observed_on": observation.get("observed_on"),
                "common_name": taxon.get("preferred_common_name"),
                "scientific_name": taxon.get("name"),
                "iconic_taxon": taxon.get("iconic_taxon_name"),
                "thumbnail_url": (
                    photos[0].get("url") if photos else None
                ),
                "longitude": coordinates[0],
                "latitude": coordinates[1],
                "positional_accuracy": accuracy,
            }
        )

    observations = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
    observations["observed_on"] = pd.to_datetime(
        observations["observed_on"],
        errors="coerce",
    )

    metadata = {
        "start_date": start_date,
        "end_date": end_date,
        "total_results": total_results,
        "downloaded_results": len(results),
        "limited": total_results > 200 * max_pages,
    }

    return observations, metadata


def summarize_species(observations: pd.DataFrame) -> pd.DataFrame:
    """Count observations by species and keep the latest photo."""
    columns = [
        "iconic_taxon",
        "common_name",
        "scientific_name",
        "observation_count",
        "most_recent_observation",
        "thumbnail_url",
    ]

    if observations.empty:
        return pd.DataFrame(columns=columns)

    observations = observations.sort_values(
        "observed_on",
        ascending=False,
    )

    summary = (
        observations.groupby(
            ["iconic_taxon", "scientific_name"],
            dropna=False,
        )
        .agg(
            common_name=("common_name", "first"),
            observation_count=("observation_id", "nunique"),
            most_recent_observation=("observed_on", "max"),
            thumbnail_url=("thumbnail_url", "first"),
        )
        .reset_index()
    )

    summary["common_name"] = summary["common_name"].fillna(
        summary["scientific_name"]
    )

    return summary


def get_top_species(
    summary: pd.DataFrame,
    top_n: int = 5,
) -> dict[str, pd.DataFrame]:
    """Return one top-species table for each nature group."""
    if top_n < 1:
        raise ValueError("top_n must be at least 1.")

    tables = {}

    for label, iconic_taxon in TAXON_GROUPS.items():
        tables[label] = (
            summary.loc[
                summary["iconic_taxon"] == iconic_taxon
            ]
            .sort_values(
                ["observation_count", "most_recent_observation"],
                ascending=[False, False],
            )
            .head(top_n)
            .reset_index(drop=True)
        )

    return tables
