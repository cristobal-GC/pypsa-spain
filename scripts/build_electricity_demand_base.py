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
    raster_fn : str
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
        Example: {"gdp": 0.6, "population": 0.4}
    regions : gpd.GeoDataFrame
        GeoDataFrame containing the regions with a 'country' column.

    Returns
    -------
    pd.Series
        Series of distribution keys indexed by region names.
    """

    gdp_weight = distribution_key["gdp"]
    pop_weight = distribution_key["population"]

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


def _nuts_bus_weights(
    gdf_regions: gpd.GeoDataFrame,
    nuts_regions: gpd.GeoDataFrame,
    raster_fn: str,
    fallback_factors: pd.Series,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Compute the share of each NUTS region's demand that belongs to each bus region.

    Bus (Voronoi) regions and NUTS regions do not nest: a single Voronoi cell may
    straddle a NUTS border. Assigning the whole cell to one NUTS region (e.g. by its
    representative point) charges that region with demand that is physically located
    in its neighbour. To avoid this, the energy atlas raster is integrated over the
    *intersection* of each (bus region, NUTS region) pair, so a straddling cell only
    ever receives the demand of the pixels it actually covers within each region.

    Where the raster carries no information for a NUTS region (it only covers EU27),
    the baseline distribution factors are used instead, apportioned to each
    intersection by its share of the bus region's area.

    Parameters
    ----------
    gdf_regions : gpd.GeoDataFrame
        Bus regions indexed and labelled by 'name', in an equal-area CRS.
    nuts_regions : gpd.GeoDataFrame
        NUTS regions indexed by 'NUTS_ID', in the same CRS as ``gdf_regions``.
    raster_fn : str
        Path to the energy atlas raster (GeoTIFF).
    fallback_factors : pd.Series
        Baseline per-bus distribution factors, indexed like ``gdf_regions``.

    Returns
    -------
    weights : pd.DataFrame
        Bus x NUTS matrix whose columns each sum to 1. Only NUTS regions that could
        be distributed are present as columns.
    dropped : dict[str, str]
        NUTS regions that could not be distributed, mapped to the reason why.
    """
    valid_regions = gdf_regions[gdf_regions.geometry.notna() & ~gdf_regions.geometry.is_empty]

    # `nuts_regions` may reach here with an unnamed index (e.g. after a `.loc` lookup),
    # so restore the label the overlay below relies on.
    nuts_frame = nuts_regions[["geometry"]].rename_axis("NUTS_ID").reset_index()

    # Intersect bus regions with NUTS regions; only overlapping pairs survive.
    pieces = gpd.overlay(
        valid_regions[["name", "geometry"]].reset_index(drop=True),
        nuts_frame,
        how="intersection",
        keep_geom_type=True,
    )
    pieces = pieces[pieces.geometry.area > 0]

    dropped = {
        nuts_id: "does not overlap any bus region"
        for nuts_id in nuts_regions.index.difference(pieces["NUTS_ID"].unique())
    }

    if pieces.empty:
        return pd.DataFrame(index=gdf_regions.index), dropped

    # Primary key: energy atlas demand contained in each intersection piece.
    with rio.open(raster_fn) as raster:
        band = raster.read(1)
        stats = zonal_stats(
            pieces, band, affine=raster.transform, nodata=-1, stats="sum"
        )
    pieces["atlas"] = [
        s["sum"] if s["sum"] is not None else np.nan for s in stats
    ]

    # Fallback key: baseline bus factor, split across pieces by area share.
    cell_area = valid_regions.geometry.area
    pieces["fallback"] = (
        pieces["name"].map(fallback_factors).to_numpy()
        * pieces.geometry.area.to_numpy()
        / pieces["name"].map(cell_area).to_numpy()
    )

    def _matrix(value: str) -> pd.DataFrame:
        return (
            pieces.pivot_table(
                index="name", columns="NUTS_ID", values=value, aggfunc="sum"
            )
            .reindex(index=gdf_regions.index)
            .reindex(columns=nuts_regions.index)
        )

    atlas = _matrix("atlas")
    fallback = _matrix("fallback")

    # Per NUTS region, prefer the raster whenever it carries any demand there.
    weights = fallback.copy()
    from_atlas = atlas.sum(min_count=1) > 0
    weights.loc[:, from_atlas[from_atlas].index] = atlas.loc[:, from_atlas[from_atlas].index]

    column_sums = weights.sum(min_count=1)
    unusable = column_sums.index[~(column_sums > 0)]
    dropped.update(
        {
            nuts_id: "no positive distribution key on any overlapping bus region"
            for nuts_id in unusable
        }
    )

    weights = weights.drop(columns=unusable)
    weights = weights.div(weights.sum(), axis=1).fillna(0.0)

    logger.info(
        f"Distribution keys for {(from_atlas & ~from_atlas.index.isin(unusable)).sum()} "
        f"NUTS regions taken from the energy atlas raster, "
        f"{(~from_atlas & ~from_atlas.index.isin(unusable)).sum()} from the fallback keys."
    )

    return weights, dropped


def upsample_load_vPyPSA_Spain(
    n: pypsa.Network,
    regions_fn: str,
    load_fn: str,
    raster_fn: str,
    gb_excel_fn: str,
    gb_geojson_fn: str,
    nuts3_fn: str,
    nutsall_fn: str,
    distribution_key: dict[str, float],
) -> xr.DataArray:
    """
    Upsample NUTS-based demand time series to base buses for any country.

    Steps:
    1. Load substation-level regions and input demand time series.
    2. Build baseline bus factors (energy atlas / GB / NUTS3 fallback).
    3. Load NUTS geometries indexed by NUTS_ID column.
    4. Extract NUTS code (AB+1-3 digits) from each load column name and aggregate
       the demand time series per NUTS region.
    5. Split each NUTS region's demand across the bus regions overlapping it, in
       proportion to the energy atlas demand contained in each overlap.
    6. Check that all demand has been placed, then return a time x bus DataArray.

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
    nutsall_fn : str
        Path to NUTS 2021 GeoJSON with 'NUTS_ID' column.
    distribution_key : dict[str, float]
        Weights for GDP/population in distribution calculation (e.g. {"gdp": 0.6, "pop": 0.4}).

    Returns
    -------
    xr.DataArray
        Demand time series with dimensions (time, bus).

    Raises
    ------
    ValueError
        If any demand could not be placed on the network, since that would silently
        lower the national total below the configured ``annual_value``.
    """
    # Step 1: Load bus regions (LV substations only) and regional load time series.
    substation_lv_i = n.buses.index[n.buses["substation_lv"]]
    gdf_regions = gpd.read_file(regions_fn).set_index("name", drop=False).reindex(substation_lv_i)
    gdf_regions = gdf_regions.to_crs(epsg=3035)
    load = pd.read_csv(load_fn, index_col=0, parse_dates=True)

    # Step 2: Compute baseline spatial factors, used where the raster has no data.
    factors = _distribution_factors(
        gdf_regions,
        raster_fn=raster_fn,
        gb_excel_fn=gb_excel_fn,
        gb_geojson_fn=gb_geojson_fn,
        nuts3_fn=nuts3_fn,
        distribution_key=distribution_key,
    )

    # Step 3: Load NUTS geometries indexed by NUTS_ID.
    nutsall = gpd.read_file(nutsall_fn)
    if "NUTS_ID" not in nutsall.columns:
        raise KeyError(
            f"Column 'NUTS_ID' not found in {nutsall_fn}. "
            f"Available columns: {list(nutsall.columns)}"
        )
    nutsall = nutsall[["NUTS_ID", "geometry"]].copy()
    nutsall["NUTS_ID"] = nutsall["NUTS_ID"].astype(str).str.upper()
    nutsall = nutsall.set_index("NUTS_ID").to_crs(gdf_regions.crs)
    if nutsall.index.has_duplicates:
        duplicates = nutsall.index[nutsall.index.duplicated()].unique().tolist()
        logger.warning(
            f"Duplicate NUTS_ID entries in {nutsall_fn} ({duplicates[:5]}...); keeping the first of each."
        )
        nutsall = nutsall[~nutsall.index.duplicated(keep="first")]

    # Step 4: Map load columns onto NUTS regions and aggregate per region.
    column_to_nuts: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for col in load.columns:
        match = re.search(r"\b([A-Z]{2}\d{1,3})\b", str(col).upper())
        if match is None:
            skipped[col] = "no NUTS code (pattern: AB + 1-3 digits) in the column name"
            continue
        nuts_id = match.group(1)
        if nuts_id not in nutsall.index:
            skipped[col] = f"NUTS code '{nuts_id}' is absent from {nutsall_fn}"
            continue
        column_to_nuts[col] = nuts_id

    load_nuts = load[list(column_to_nuts)].rename(columns=column_to_nuts)
    load_nuts = load_nuts.T.groupby(level=0).sum().T

    # Step 5: Distribute each NUTS region's demand across the bus regions it overlaps.
    weights, dropped = _nuts_bus_weights(
        gdf_regions,
        nutsall.loc[load_nuts.columns],
        raster_fn=raster_fn,
        fallback_factors=factors,
    )
    for col, nuts_id in column_to_nuts.items():
        if nuts_id in dropped:
            skipped[col] = f"NUTS region '{nuts_id}' {dropped[nuts_id]}"

    load_nuts = load_nuts[weights.columns]
    load_by_bus = pd.DataFrame(
        load_nuts.to_numpy() @ weights.to_numpy().T,
        index=load_nuts.index,
        columns=weights.index,
    )

    # Step 6: Verify that no demand was lost on the way, then assemble the output.
    expected = load.to_numpy().sum()
    delivered = load_by_bus.to_numpy().sum()

    if skipped:
        lost = sum(load[col].sum() for col in skipped)
        detail = "\n".join(
            f"  - '{col}' ({load[col].sum() / 1e6:.3f} TWh): {reason}"
            for col, reason in sorted(skipped.items())
        )
        if lost > 1e-6 * expected:
            raise ValueError(
                f"Could not place {lost / 1e6:.3f} TWh of "
                f"{expected / 1e6:.3f} TWh ({lost / expected:.2%}) of electricity demand "
                f"on the network. The national total would silently fall below the "
                f"configured `annual_value`. Offending load columns:\n{detail}"
            )
        logger.warning(f"Ignoring load columns carrying no demand:\n{detail}")

    if not np.isclose(delivered, expected, rtol=1e-6):
        raise ValueError(
            f"Demand upsampling is not energy-conserving: {delivered / 1e6:.3f} TWh "
            f"distributed over the buses against {expected / 1e6:.3f} TWh in {load_fn}."
        )

    logger.info(
        f"Distributed {delivered / 1e6:.3f} TWh of electricity demand from "
        f"{len(weights.columns)} NUTS regions over {(load_by_bus.sum() > 0).sum()} "
        f"of {len(gdf_regions)} bus regions."
    )

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
            nutsall_fn=snakemake.input.nutsall,
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
