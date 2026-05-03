# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Build mapping between cutout grid cells and population (total, urban, rural).
"""

import logging

import atlite
import country_converter as coco
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from scripts._helpers import configure_logging, load_cutout, set_scenario_config

logger = logging.getLogger(__name__)

cc = coco.CountryConverter()

if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake(
            "build_population_layouts",
        )

    configure_logging(snakemake)
    set_scenario_config(snakemake)
    logging.getLogger("country_converter").setLevel(logging.CRITICAL)

    cutout = load_cutout(snakemake.input.cutout)

    grid_cells = cutout.grid.geometry

    # nuts3 has columns country, gdp, pop, geometry
    # population is given in dimensions of 1e3=k
    nuts3 = gpd.read_file(snakemake.input.nuts3_shapes).set_index("index")

    # Indicator matrix NUTS3 -> grid cells
    I = atlite.cutout.compute_indicatormatrix(nuts3.geometry, grid_cells)  # noqa: E741

    # Indicator matrix grid_cells -> NUTS3; inprinciple Iinv*I is identity
    # but imprecisions mean not perfect
    Iinv = cutout.indicatormatrix(nuts3.geometry)

    countries = np.sort(nuts3.country.unique())

    urban_fraction = pd.read_csv(snakemake.input.urban_percent, skiprows=4)
    iso3 = urban_fraction["Country Code"]
    urban_fraction["iso2"] = cc.convert(names=iso3, src="ISO3", to="ISO2")
    urban_fraction = (
        urban_fraction.query("iso2 in @countries").set_index("iso2")["2019"].div(100)
    )
    if "XK" in countries:
        urban_fraction["XK"] = urban_fraction["RS"]

    # population in each grid cell
    pop_cells = pd.Series(I.dot(nuts3["pop"]))

    # in km^2
    cell_areas = grid_cells.to_crs(3035).area / 1e6

    # pop per km^2
    density_cells = pop_cells / cell_areas

    # rural or urban population in grid cell
    pop_rural = pd.Series(0.0, density_cells.index)
    pop_urban = pd.Series(0.0, density_cells.index)

    for ct in countries:
        logger.debug(
            f"The urbanization rate for {ct} is {round(urban_fraction[ct] * 100)}%"
        )

        indicator_nuts3_ct = nuts3.country.apply(lambda x: 1.0 if x == ct else 0.0)

        indicator_cells_ct = pd.Series(Iinv.T.dot(indicator_nuts3_ct))

        density_cells_ct = indicator_cells_ct * density_cells

        pop_cells_ct = indicator_cells_ct * pop_cells

        # correct for imprecision of Iinv*I
        pop_ct = nuts3.loc[nuts3.country == ct, "pop"].sum()
        if pop_cells_ct.sum() != 0:
            pop_cells_ct *= pop_ct / pop_cells_ct.sum()

        # The first low density grid cells to reach rural fraction are rural
        asc_density_i = density_cells_ct.sort_values().index
        asc_density_cumsum = (
            pop_cells_ct.iloc[asc_density_i].cumsum() / pop_cells_ct.sum()
        )
        rural_fraction_ct = 1 - urban_fraction[ct]
        pop_ct_rural_b = asc_density_cumsum < rural_fraction_ct
        pop_ct_urban_b = ~pop_ct_rural_b

        pop_ct_rural_b[indicator_cells_ct == 0.0] = False
        pop_ct_urban_b[indicator_cells_ct == 0.0] = False

        pop_rural += pop_cells_ct.where(pop_ct_rural_b, 0.0)
        pop_urban += pop_cells_ct.where(pop_ct_urban_b, 0.0)

    pop_cells = {"total": pop_cells}
    pop_cells["rural"] = pop_rural
    pop_cells["urban"] = pop_urban

    for key, pop in pop_cells.items():
        ycoords = ("y", cutout.coords["y"].data)
        xcoords = ("x", cutout.coords["x"].data)
        values = pop.values.reshape(cutout.shape)
        layout = xr.DataArray(values, [ycoords, xcoords])

        layout.to_netcdf(snakemake.output[f"pop_layout_{key}"])



    #################### PyPSA-Spain
    #
    # Override layouts using municipality-level population data (Spain)
    #
    #
    pop_layouts_HR = snakemake.params.pop_layouts_HR
    if pop_layouts_HR.get("enable", False):
        logger.info(
            "########## PyPSA-Spain [build_population_layouts]: Recomputing population layouts from municipality-level data "
            f"({snakemake.input.pop_municipalities})"
        )

        municipalities = gpd.read_file(snakemake.input.pop_municipalities)

        # Drop municipalities outside the cutout footprint (Canarias, Ceuta,
        # Melilla) so their population is not silently lost when projecting to
        # grid cells.
        excluded_ccaa = [
            "Canarias",
            "Ciudad Autónoma de Ceuta",
            "Ciudad Autónoma de Melilla",
        ]
        n_before = len(municipalities)
        pop_before = municipalities["pob_23"].sum()
        municipalities = municipalities[
            ~municipalities["ccaa"].isin(excluded_ccaa)
        ].reset_index(drop=True)
        n_excluded = n_before - len(municipalities)
        pop_excluded = pop_before - municipalities["pob_23"].sum()
        logger.info(
            "########## PyPSA-Spain [build_population_layouts]: "
            f"excluded {n_excluded} municipalities in "
            f"{excluded_ccaa} ({len(municipalities)} remaining); "
            f"excluded population: {pop_excluded:,.0f} hab "
            f"({municipalities['pob_23'].sum():,.0f} remaining)"
        )

        # accurate areas in km^2 using ETRS89 / LAEA Europe (EPSG:3035)
        muni_area_km2 = municipalities.geometry.to_crs(3035).area / 1e6
        muni_density = municipalities["pob_23"] / muni_area_km2

        logger.info(
            "########## PyPSA-Spain [build_population_layouts]:\n"
            "Municipality density (hab/km^2): "
            f"min={muni_density.min():.2f}, "
            f"p25={muni_density.quantile(0.25):.2f}, "
            f"median={muni_density.median():.2f}, "
            f"p75={muni_density.quantile(0.75):.2f}, "
            f"max={muni_density.max():.2f}"
        )

        # Urban/rural classification based on a target urban population fraction
        # taken from config (pypsa_spain.pop_layouts_HR.urban_fraction).
        target_urban_fraction = float(pop_layouts_HR["urban_fraction"])
        worldbank_urban_fraction = float(urban_fraction.get("ES", float("nan")))

        logger.info(
            "########## PyPSA-Spain [build_population_layouts]: "
            f"urban fraction — World Bank (Spain): {worldbank_urban_fraction:.3f}, "
            f"config (used): {target_urban_fraction:.3f}"
        )

        # Sort municipalities by density (descending) and tag the highest-density
        # ones as urban until their cumulative population reaches the target.
        order_desc = np.argsort(-muni_density.values)
        sorted_pop = municipalities["pob_23"].astype(float).values[order_desc]
        sorted_dens = muni_density.values[order_desc]
        cum_share = np.cumsum(sorted_pop) / sorted_pop.sum()

        is_urban_sorted = cum_share < target_urban_fraction
        # include the boundary municipality so the cumulative share
        # reaches/exceeds the target instead of falling just short
        first_above = np.argmax(~is_urban_sorted) if (~is_urban_sorted).any() else len(is_urban_sorted)
        if first_above < len(is_urban_sorted):
            is_urban_sorted[first_above] = True

        is_urban_arr = np.zeros(len(muni_density), dtype=bool)
        is_urban_arr[order_desc[is_urban_sorted]] = True
        is_urban = pd.Series(is_urban_arr, index=muni_density.index)

        # threshold density = lowest density among urban municipalities
        density_threshold = (
            float(sorted_dens[is_urban_sorted][-1]) if is_urban_sorted.any() else float("nan")
        )

        urban_muni_pct = 100 * is_urban.sum() / len(is_urban)
        logger.info(
            f"Urban/rural split: {is_urban.sum()} urban ({urban_muni_pct:.1f}%) "
            f"/ {(~is_urban).sum()} rural municipalities; "
            f"boundary density = {density_threshold:.2f} hab/km^2"
        )

        # Persist urban/rural municipalities as CSVs (codmun_ine is the unique
        # INE code; nombre/ccaa are kept for human inspection). Geometry is not
        # stored: build_clustered_population_layouts re-loads the shapefile and
        # joins by codmun_ine to perform area-weighted aggregation per region.
        muni_csv = municipalities[["codmun_ine", "nombre", "ccaa", "pob_23"]].copy()
        muni_csv["pob_23"] = muni_csv["pob_23"].astype(float)
        muni_csv.loc[is_urban.values].to_csv(
            snakemake.output.pop_municipalities_urban, index=False
        )
        muni_csv.loc[~is_urban.values].to_csv(
            snakemake.output.pop_municipalities_rural, index=False
        )

        # Indicator matrix municipalities -> grid cells:
        # entry[i, j] = area(intersect(muni[j], cell[i])) / area(muni[j])
        # so dot(pop) distributes each municipality's population homogeneously
        # across the grid cells it overlaps.
        I_muni = atlite.cutout.compute_indicatormatrix(  # noqa: E741
            municipalities.geometry, grid_cells
        )

        # match the original NUTS3 convention: layouts are in thousands of inhabitants
        pop_muni = municipalities["pob_23"].astype(float).values / 1e3
        pop_muni_urban = np.where(is_urban.values, pop_muni, 0.0)
        pop_muni_rural = np.where(is_urban.values, 0.0, pop_muni)

        pop_cells_muni = {
            "total": pd.Series(I_muni.dot(pop_muni)),
            "urban": pd.Series(I_muni.dot(pop_muni_urban)),
            "rural": pd.Series(I_muni.dot(pop_muni_rural)),
        }

        for key, pop in pop_cells_muni.items():
            ycoords = ("y", cutout.coords["y"].data)
            xcoords = ("x", cutout.coords["x"].data)
            values = pop.values.reshape(cutout.shape)
            layout = xr.DataArray(values, [ycoords, xcoords])

            layout.to_netcdf(snakemake.output[f"pop_layout_{key}"])


        # ---------- diagnostic plots ----------
        import matplotlib as mpl

        mpl.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import LogNorm

        # plot in lon/lat (EPSG:4326) so we can use geographic bounds
        muni_plot = municipalities.to_crs(4326)
        muni_plot["density"] = muni_density.values
        muni_plot["is_urban"] = is_urban.values

        # map extent in lon/lat: [lon_min, lon_max, lat_min, lat_max]
        map_bounds = (-9.93, 4.11, 35.61, 44.18)

        def _apply_map_extent(ax):
            ax.set_xlim(map_bounds[0], map_bounds[1])
            ax.set_ylim(map_bounds[2], map_bounds[3])
            ax.set_aspect("equal")

        total_pop = float(municipalities["pob_23"].sum())
        urban_pop = float(municipalities.loc[is_urban, "pob_23"].sum())
        rural_pop = total_pop - urban_pop
        urban_pct = 100 * urban_pop / total_pop
        rural_pct = 100 * rural_pop / total_pop

        n_total = len(is_urban)
        n_urban = int(is_urban.sum())
        n_rural = n_total - n_urban
        rural_muni_pct = 100 - urban_muni_pct

        split_info = (
            f"{n_urban} urban ({urban_muni_pct:.1f}%) / "
            f"{n_rural} rural municipalities; "
            f"boundary density = {density_threshold:.1f} hab/km²"
        )

        urban_title = (
            f"Urban municipalities: {n_urban} out of {n_total} ({urban_muni_pct:.1f}%)\n"
            f"Urban population: {urban_pop / 1e6:.2f} Mill.hab. out of "
            f"{total_pop / 1e6:.2f} Mill.hab. ({urban_pct:.1f}%)\n"
            f"Urban density >= {density_threshold:.1f} hab/km²"
        )
        rural_title = (
            f"Rural municipalities: {n_rural} out of {n_total} ({rural_muni_pct:.1f}%)\n"
            f"Rural population: {rural_pop / 1e6:.2f} Mill.hab. out of "
            f"{total_pop / 1e6:.2f} Mill.hab. ({rural_pct:.1f}%)\n"
            f"Rural density <= {density_threshold:.1f} hab/km²"
        )

        # Map: urban municipalities highlighted
        fig, ax = plt.subplots(figsize=(10, 8))
        muni_plot[~muni_plot["is_urban"]].plot(
            ax=ax, color="lightgrey", edgecolor="none"
        )
        muni_plot[muni_plot["is_urban"]].plot(
            ax=ax, color="crimson", edgecolor="none"
        )
        _apply_map_extent(ax)
        ax.set_axis_off()
        ax.set_title(urban_title)
        fig.tight_layout()
        fig.savefig(snakemake.output.pop_map_urban, dpi=150)
        plt.close(fig)

        # Map: rural municipalities highlighted
        fig, ax = plt.subplots(figsize=(10, 8))
        muni_plot[muni_plot["is_urban"]].plot(
            ax=ax, color="lightgrey", edgecolor="none"
        )
        muni_plot[~muni_plot["is_urban"]].plot(
            ax=ax, color="forestgreen", edgecolor="none"
        )
        _apply_map_extent(ax)
        ax.set_axis_off()
        ax.set_title(rural_title)
        fig.tight_layout()
        fig.savefig(snakemake.output.pop_map_rural, dpi=150)
        plt.close(fig)

        # Map: density (log-scaled colormap)
        # figsize chosen close to the data bbox aspect ratio to minimise margins
        bbox_ratio = (map_bounds[1] - map_bounds[0]) / (
            map_bounds[3] - map_bounds[2]
        )
        fig, ax = plt.subplots(figsize=(7 * bbox_ratio + 1.0, 7))
        density_for_plot = muni_plot["density"].clip(lower=1e-3)
        muni_plot.assign(density_clip=density_for_plot).plot(
            ax=ax,
            column="density_clip",
            cmap="viridis",
            norm=LogNorm(
                vmin=max(density_for_plot.min(), 1e-1),
                vmax=density_for_plot.max(),
            ),
            legend=True,
            legend_kwds={
                "label": "Population density (hab/km²)",
                "shrink": 0.8,
            },
            edgecolor="none",
        )
        _apply_map_extent(ax)
        ax.set_axis_off()
        ax.set_title(f"Municipality population density\n{n_total} municipalities")
        fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.01)
        fig.savefig(snakemake.output.pop_map_density, dpi=150, bbox_inches="tight")
        plt.close(fig)

        # Decreasing-step plot: density vs cumulative population
        order = np.argsort(-muni_density.values)  # descending density
        sorted_pop = municipalities["pob_23"].astype(float).values[order]
        sorted_dens = muni_density.values[order]

        left_edges = np.concatenate(([0.0], np.cumsum(sorted_pop)[:-1]))

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.bar(
            left_edges,
            sorted_dens,
            width=sorted_pop,
            align="edge",
            color="steelblue",
            edgecolor="none",
        )
        ax.axhline(
            density_threshold,
            color="red",
            linestyle="--",
            linewidth=1.2,
            label=(
                f"Boundary density = {density_threshold:.1f} hab/km² "
                f"(target urban fraction = {target_urban_fraction:.2f})"
            ),
        )
        ax.set_yscale("log")
        ax.set_xlim(0, total_pop)
        ax.set_xlabel("Cumulative population (sorted by descending density)")
        ax.set_ylabel("Population density (hab/km²)")
        ax.set_title(f"Density vs. population\n{split_info}")
        ax.legend(loc="upper right")
        fig.tight_layout()
        fig.savefig(snakemake.output.pop_density_steps, dpi=150)
        plt.close(fig)
    else:
        # Placeholder outputs so declared outputs always exist
        from pathlib import Path

        for key in (
            "pop_map_urban",
            "pop_map_rural",
            "pop_map_density",
            "pop_density_steps",
        ):
            Path(snakemake.output[key]).parent.mkdir(parents=True, exist_ok=True)
            Path(snakemake.output[key]).touch()

        # Empty CSV placeholders (header only) for the muni-level outputs
        for key in ("pop_municipalities_urban", "pop_municipalities_rural"):
            Path(snakemake.output[key]).parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                columns=["codmun_ine", "nombre", "ccaa", "pob_23"]
            ).to_csv(snakemake.output[key], index=False)
    #
    #
    ######################
