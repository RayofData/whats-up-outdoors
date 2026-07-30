![What's UP Outdoors banner](assets/banner.png)

# What’s UP Outdoors: Upper Peninsula Trail Explorer

What’s UP Outdoors is a Python and Streamlit application for finding hiking trails and nearby nature observations in Michigan’s Upper Peninsula.

## Project Goal

Users will be able to enter a destination ZIP code, choose a search radius, and view nearby hiking trails on a map. Selecting a trail will display basic trail information and commonly recorded nature observations from iNaturalist.

## Planned MVP Features

- Search for trails by Upper Peninsula ZIP code
- Choose a 10, 25, or 50 mile search radius
- View nearby trails in a table and on a map
- Filter trails by length category
- Display trail length, surface, accessibility, and distance
- View nearby iNaturalist observations
- Show commonly recorded species and nature categories

## Trail Length Categories

- **Short:** 2 miles or less
- **Medium:** More than 2 miles and up to 7 miles
- **Long:** More than 7 miles

These categories are based on trail length only and do not represent terrain difficulty.

## Data Sources

- Michigan DNR Hiking Trails Open Data
- iNaturalist Observations API
- GeoNames postal-code data through `pgeocode`

## Technologies

- Python
- Streamlit
- pandas
- GeoPandas
- Shapely
- Requests
- pgeocode
- GeoParquet

## Project Status

This project is currently in development. The first release is planned for September 2026.

## Running the Project

Setup instructions will be added as the project is developed.

## Limitations

- ZIP-code distances are approximate and are measured from the ZIP-code center.
- iNaturalist data represents recorded observations, not the probability of seeing a species.
- Trail length categories do not account for elevation, terrain, or current trail conditions.

## Future Plans

A later version may include weather forecasts, explainable trail rankings, and an AI assistant that explains nearby observations and trail features.
