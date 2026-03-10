# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Builds the electricity demand for base regions based on population and GDP.
"""

import logging
import re

import country_converter as coco
import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa
import rasterio as rio
import xarray as xr
from rasterstats import zonal_stats

from scripts._helpers import configure_logging, set_scenario_config

logger = logging.getLogger(__name__)

cc = coco.CountryConverter()


def normed(s: pd.Series) -> pd.Series:
    return s / s.sum()


def redistribute_attribute(
    orig: gpd.GeoDataFrame, dest: gpd.GeoDataFrame, attr: str
) -> pd.Series:
    """
    Redistributes an attribute from origin GeoDataFrame to destination GeoDataFrame based on overlapping areas.

    Computes the intersection between geometries and proportionally assigns the attribute values
    according to the share of overlapping area.

    Parameters
    ----------
    orig : gpd.GeoDataFrame
        Source GeoDataFrame containing the attribute to redistribute
    dest : gpd.GeoDataFrame
        Target GeoDataFrame to receive redistributed values
    attr : str
        Name of the attribute column to redistribute

    Returns
    -------
    pd.Series
        Redistributed attribute values indexed by destination's 'name' column
    """
    if orig.crs != 3035:
        orig = orig.to_crs(epsg=3035)
    if dest.crs != 3035:
        dest = dest.to_crs(epsg=3035)
    orig["area_orig"] = orig.area
    overlay = gpd.overlay(dest, orig, keep_geom_type=False)
    overlay["share"] = overlay.area / overlay.area_orig
    overlay["attr"] = overlay[attr].mul(overlay.share)
    return overlay.dissolve("name", aggfunc="sum")["attr"]


def energy_atlas_distribution_keys(
    raster_fn: str, regions: gpd.GeoDataFrame
) -> pd.Series:
    """
    Calculate distribution keys for regions based on energy atlas raster data.

    Parameters
    ----------
    fn : str
        File path to the raster data (GeoTIFF format).
    regions : gpd.GeoDataFrame
        GeoDataFrame containing the regions with a 'country' column.

    Returns
    -------
    pd.Series
        Series of distribution keys indexed by region names.
        Sum of keys per country equals 1 (unless omitted islands in regions).
    """

    raster = rio.open(raster_fn)
    band = raster.read(1)

    distribution_keys = []
    for country, group in regions.groupby("country"):
        if country not in cc.EU27as("ISO2").ISO2.to_list():
            continue
        weights = zonal_stats(
            group, band, affine=raster.transform, nodata=-1, stats="sum"
        )
        group["weights"] = [w["sum"] for w in weights]
        distribution_keys.append(group["weights"] / group["weights"].sum())
    distribution_keys = pd.concat(distribution_keys).reindex(regions.index)
    return distribution_keys


def gb_distribution_keys(
    excel_fn: str, geojson_fn: str, regions: gpd.GeoDataFrame
) -> pd.Series:
    """
    Calculate distribution keys for Great Britain regions based on electricity consumption statistics.

    Parameters
    ----------
    excel_fn : str
        File path to the Excel file containing electricity consumption by local authority districts.
    geojson_fn : str
        File path to the GeoJSON file containing geometries of local authority districts.
    regions : gpd.GeoDataFrame
        GeoDataFrame containing the network regions.

    Returns
    -------
    pd.Series
        Series of distribution keys indexed by region names.
    """

    df = pd.read_excel(excel_fn, skiprows=4, sheet_name="2019")
    df = df.loc[~df["Local authority"].isin(["All local authorities", "Unallocated"])]
    gdf = gpd.read_file(geojson_fn).to_crs(epsg=3035)
    gdf = gdf.rename(columns={"LAD24CD": "Code"}).merge(df, on="Code")

    attr = "Total consumption\n(GWh):\nAll meters"
    redistributed = redistribute_attribute(gdf, regions.reset_index(drop=True), attr)
    distribution_keys = normed(redistributed)
    return distribution_keys


def nuts3_distribution_keys(
    nuts3_fn: str, distribution_key: dict[str, float], regions: gpd.GeoDataFrame
) -> pd.Series:
    """
    Calculate distribution keys for regions based on NUTS3 data (GDP and population).

    Parameters
    ----------
    nuts3_fn : str
        File path to the NUTS3 GeoJSON file containing geometries and attributes.
    distribution_key : dict[str, float]
        Weights for GDP and population in the distribution key calculation.
        Example: {"gdp": 0.6, "pop": 0.4}
    regions : gpd.GeoDataFrame
        GeoDataFrame containing the regions with a 'country' column.

    Returns
    -------
    pd.Series
        Series of distribution keys indexed by region names.
    """

    gdp_weight = distribution_key.get("gdp", 0.6)
    pop_weight = distribution_key.get("pop", 0.4)

    nuts3 = gpd.read_file(nuts3_fn).to_crs(epsg=3035)
    nuts3.rename(columns={"name": "nuts3_name"}, inplace=True)

    regions["pop"] = redistribute_attribute(
        nuts3, regions.reset_index(drop=True), "pop"
    )
    regions["gdp"] = redistribute_attribute(
        nuts3, regions.reset_index(drop=True), "gdp"
    )

    nuts3_keys = []
    for country, group in regions.groupby("country"):
        factors = normed(
            gdp_weight * normed(group["gdp"]) + pop_weight * normed(group["pop"])
        )
        nuts3_keys.append(factors)
    return pd.concat(nuts3_keys).reindex(regions.index)


def upsample_load(
    n: pypsa.Network,
    regions_fn: str,
    load_fn: str,
    raster_fn: str,
    gb_excel_fn: str,
    gb_geojson_fn: str,
    nuts3_fn: str,
    distribution_key: dict[str, float],
    substation_only: bool = False,
) -> xr.DataArray:
    regions = gpd.read_file(regions_fn).set_index("name", drop=False).to_crs(epsg=3035)

    if substation_only:
        substation_lv_i = n.buses.index[n.buses["substation_lv"]]
        regions = regions.reindex(substation_lv_i)
    load = pd.read_csv(load_fn, index_col=0, parse_dates=True)

    ea_keys = energy_atlas_distribution_keys(raster_fn, regions)
    gb_keys = gb_distribution_keys(gb_excel_fn, gb_geojson_fn, regions)
    nuts3_keys = nuts3_distribution_keys(nuts3_fn, distribution_key, regions)

    factors = ea_keys.combine_first(gb_keys).combine_first(nuts3_keys)

    # sanitize: need to renormalize since `gb_keys` only cover Great Britain
    # and Northern Ireland is taken from `nuts3_keys`
    if "GB" in regions.country:
        uk_regions_i = regions.query("country == 'GB'").index
        uk_weights = factors.loc[uk_regions_i].sum()
        factors.loc[uk_regions_i] /= uk_weights

    data_arrays = []

    for cntry, group in regions.geometry.groupby(regions.country):
        if cntry not in load.columns:
            logger.warning(f"Cannot upsample load for {cntry}: no load data defined")
            continue

        load_ct = load[cntry]
        factors_ct = factors.loc[group.index]

        data_arrays.append(
            xr.DataArray(
                factors_ct.values * load_ct.values[:, np.newaxis],
                dims=["time", "bus"],
                coords={"time": load_ct.index.values, "bus": factors_ct.index.values},
            )
        )

    return xr.concat(data_arrays, dim="bus")





################################################## PyPSA-Spain
#
# Function to attach electricity demand according to PyPSA-Spain methodology
#
#

# Auxiliary function (code extracted from pypsa-eur methodology, see above) to compute distribution factors, required for PyPSA-Spain demand upsampling
def _distribution_factors(
    regions: gpd.GeoDataFrame,
    raster_fn: str,
    gb_excel_fn: str,
    gb_geojson_fn: str,
    nuts3_fn: str,
    distribution_key: dict[str, float],
) -> pd.Series:
    ea_keys = energy_atlas_distribution_keys(raster_fn, regions)
    gb_keys = gb_distribution_keys(gb_excel_fn, gb_geojson_fn, regions)
    nuts3_keys = nuts3_distribution_keys(nuts3_fn, distribution_key, regions)

    factors = ea_keys.combine_first(gb_keys).combine_first(nuts3_keys)

    if "GB" in regions.country.values:
        uk_regions_i = regions.query("country == 'GB'").index
        uk_weights = factors.loc[uk_regions_i].sum()
        factors.loc[uk_regions_i] /= uk_weights

    return factors


def upsample_load_vPyPSA_Spain(
    n: pypsa.Network,
    regions_fn: str,
    load_fn: str,
    raster_fn: str,
    gb_excel_fn: str,
    gb_geojson_fn: str,
    nuts3_fn: str,
    nuts2021_fn: str,
    distribution_key: dict[str, float],
) -> xr.DataArray:
    """
    Upsample NUTS-based demand time series to base buses for any country.

    Steps:
    1. Load substation-level regions and input demand time series.
    2. Build baseline bus factors (energy atlas / GB / NUTS3 fallback).
    3. Load NUTS geometries indexed by NUTS_ID column.
    4. Extract NUTS code (AB+1-3 digits) from each load column name.
    5. Select buses inside that NUTS geometry (fallback: intersecting buses).
    6. Renormalize factors on selected buses so weights sum to 1.
    7. Distribute the column time series across selected buses and accumulate.
    8. Return a time x bus DataArray.

    Parameters
    ----------
    n : pypsa.Network
        Network with bus metadata.
    regions_fn : str
        Path to regions GeoJSON (base Voronoi cells).
    load_fn : str
        Path to CSV with demand time series. Columns should contain NUTS codes.
    raster_fn : str
        Path to energy atlas raster for distribution keys.
    gb_excel_fn : str
        Path to GB consumption Excel for distribution keys.
    gb_geojson_fn : str
        Path to GB local authority GeoJSON.
    nuts3_fn : str
        Path to NUTS3 shapes with GDP/population for fallback distribution keys.
    nuts2021_fn : str
        Path to NUTS 2021 GeoJSON with 'NUTS_ID' column.
    distribution_key : dict[str, float]
        Weights for GDP/population in distribution calculation (e.g. {"gdp": 0.6, "pop": 0.4}).

    Returns
    -------
    xr.DataArray
        Demand time series with dimensions (time, bus).
    """
    # Step 1: Load bus regions (LV substations only) and regional load time series.
    substation_lv_i = n.buses.index[n.buses["substation_lv"]]
    gdf_regions = gpd.read_file(regions_fn).set_index("name", drop=False).reindex(substation_lv_i)
    gdf_regions = gdf_regions.to_crs(epsg=3035)
    load = pd.read_csv(load_fn, index_col=0, parse_dates=True)
       
    # Step 2: Compute baseline spatial factors for all buses.
    factors = _distribution_factors(
        gdf_regions,
        raster_fn=raster_fn,
        gb_excel_fn=gb_excel_fn,
        gb_geojson_fn=gb_geojson_fn,
        nuts3_fn=nuts3_fn,
        distribution_key=distribution_key,
    )

    # Step 3: Load NUTS geometries indexed by NUTS_ID.
    nuts = gpd.read_file(nuts2021_fn)
    if "NUTS_ID" not in nuts.columns:
        raise KeyError(
            f"Column 'NUTS_ID' not found in {nuts2021_fn}. "
            f"Available columns: {list(nuts.columns)}"
        )
    nuts = nuts[["NUTS_ID", "geometry"]].copy()
    nuts["NUTS_ID"] = nuts["NUTS_ID"].astype(str).str.upper()
    nuts = nuts.set_index("NUTS_ID").to_crs(gdf_regions.crs)

    # Step 4: Prepare bus representative points and output container.
    bus_points = gdf_regions.geometry.representative_point()
    load_by_bus = pd.DataFrame(0.0, index=load.index, columns=gdf_regions.index)

    # Step 5: Distribute each NUTS column to buses inside its geometry.
    for col in load.columns:
        # Extract NUTS code (2 letters + 1-3 digits) from column name.
        col_upper = str(col).upper()
        match = re.search(r"\b([A-Z]{2}\d{1,3})\b", col_upper)
        nuts_id = match.group(1) if match else None
        if nuts_id is None:
            logger.warning(
                f"Could not parse NUTS code (pattern: AB + 1-3 digits) from load column '{col}'. Skipping."
            )
            continue

        if nuts_id not in nuts.index:
            logger.warning(
                f"NUTS code '{nuts_id}' from column '{col}' not found in NUTS geometry file. Skipping."
            )
            continue

        # Select buses inside the NUTS region (fallback to intersects).
        region_geom = nuts.at[nuts_id, "geometry"]
        bus_mask = bus_points.within(region_geom)

        if not bus_mask.any():
            bus_mask = gdf_regions.geometry.intersects(region_geom)

        selected_buses = gdf_regions.index[bus_mask]
        if len(selected_buses) == 0:
            logger.warning(f"No buses found inside NUTS region '{nuts_id}'. Skipping.")
            continue

        selected_factors = factors.loc[selected_buses].dropna()
        selected_buses = selected_factors.index
        if len(selected_buses) == 0:
            logger.warning(
                f"No valid factors found for buses in NUTS region '{nuts_id}'. Skipping."
            )
            continue

        factor_sum = selected_factors.sum()
        if factor_sum <= 0:
            logger.warning(
                f"Non-positive factor sum for NUTS region '{nuts_id}'. Skipping."
            )
            continue

        # Renormalize selected factors and accumulate the time series.
        weights = selected_factors / factor_sum
        load_by_bus.loc[:, selected_buses] += np.outer(load[col].values, weights.values)

    # Step 6: Return final time x bus demand array.
    return xr.DataArray(
        load_by_bus.values,
        dims=["time", "bus"],
        coords={"time": load_by_bus.index.values, "bus": load_by_bus.columns.values},
    )
#
#
#
########################################





if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_electricity_demand_base")
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    params = snakemake.params

    n = pypsa.Network(snakemake.input.base_network)

    ################################################## PyPSA-Spain
    #
    # Attach electricity demand according to PyPSA-Spain methodology
    #

    electricity_demand = snakemake.params.electricity_demand


    if electricity_demand['enable']:

        logger.info(f'########## [PyPSA-Spain] <build_electricity_demand_base> INFO: Using upsample_load_vPyPSA_Spain to add electricity demand in ES.')

        load = upsample_load_vPyPSA_Spain(
            n,
            regions_fn=snakemake.input.regions,
            load_fn=snakemake.input.load,
            raster_fn=snakemake.input.raster,
            gb_excel_fn=snakemake.input.gb_excel,
            gb_geojson_fn=snakemake.input.gb_geojson,
            nuts3_fn=snakemake.input.nuts3,
            nuts2021_fn=snakemake.input.nuts2021,
            distribution_key=params.distribution_key,
        )

    else:
        load = upsample_load(
            n,
            regions_fn=snakemake.input.regions,
            load_fn=snakemake.input.load,
            raster_fn=snakemake.input.raster,
            gb_excel_fn=snakemake.input.gb_excel,
            gb_geojson_fn=snakemake.input.gb_geojson,
            nuts3_fn=snakemake.input.nuts3,
            distribution_key=params.distribution_key,
            substation_only=params.substation_only,
        )

    #
    #        
    ##################################################



    load.name = "electricity demand (MW)"
    comp = dict(zlib=True, complevel=9, least_significant_digit=5)
    load.to_netcdf(snakemake.output[0], encoding={load.name: comp})
