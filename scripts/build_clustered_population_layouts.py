# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Build population layouts for all clustered model regions as total as well as
split by urban and rural population.
"""

import logging

import atlite
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from scripts._helpers import configure_logging, load_cutout, set_scenario_config

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_clustered_population_layouts", clusters=48)

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    cutout = load_cutout(snakemake.input.cutout)

    clustered_regions = (
        gpd.read_file(snakemake.input.regions_onshore).set_index("name").buffer(0)
    )

    I = cutout.indicatormatrix(clustered_regions)  # noqa: E741

    pop = {}
    for item in ["total", "urban", "rural"]:
        pop_layout = xr.open_dataarray(snakemake.input[f"pop_layout_{item}"])
        pop[item] = I.dot(pop_layout.stack(spatial=("y", "x")))

    pop = pd.DataFrame(pop, index=clustered_regions.index)

    pop["ct"] = pop.index.str[:2]
    country_population = pop.total.groupby(pop.ct).sum()
    pop["fraction"] = pop.total / pop.ct.map(country_population)

    pop.to_csv(snakemake.output.clustered_pop_layout)



    #################### PyPSA-Spain
    #
    # Override clustered layout using municipality-level data (Spain).
    #
    # Rationale: the cell-based aggregation above uses an indicator matrix
    # I[r, c] = area(intersect(cell_c, region_r)) / area(cell_c), which assumes
    # population is uniformly distributed over each cell. For coastal cells
    # populated only on a small (onshore) sliver, this loses part of the
    # population that was deposited on those slivers in build_population_layouts.
    #
    # Here we skip the cell intermediation: build_population_layouts.py exports
    # two CSVs (urban / rural codmun_ine + nombre + ccaa + pob_23), and we
    # combine them with the original municipality shapefile to perform an
    # area-weighted aggregation per clustered region using atlite's indicator
    # matrix:
    #
    #     M[r, m] = area(intersect(region_r, muni_m)) / area(muni_m)
    #     pop_region = M @ pop_muni
    #
    # Each municipality's population is split across the regions that
    # intersect it in proportion to area, exactly preserving the total
    # population of fully-covered municipalities.
    #
    pop_layouts_HR = snakemake.params.pop_layouts_HR
    if pop_layouts_HR.get("enable", False):
        logger.info(
            "########## PyPSA-Spain [build_clustered_population_layouts]: "
            "Recomputing clustered population from municipality-level data"
        )

        # codmun_ine is a string with leading zeros — preserve them
        urban_ids = pd.read_csv(
            snakemake.input.pop_municipalities_urban, dtype={"codmun_ine": str}
        )["codmun_ine"]
        rural_ids = pd.read_csv(
            snakemake.input.pop_municipalities_rural, dtype={"codmun_ine": str}
        )["codmun_ine"]

        all_munis = gpd.read_file(snakemake.input.pop_municipalities)
        all_munis["codmun_ine"] = all_munis["codmun_ine"].astype(str)
        all_munis = all_munis.set_index("codmun_ine").to_crs(3035)

        regions_3035 = clustered_regions.to_crs(3035)

        def _aggregate(ids, label):
            if len(ids) == 0:
                return pd.Series(0.0, index=clustered_regions.index, name=label)
            sub = all_munis.loc[ids]
            pop_kinhab = sub["pob_23"].astype(float).values / 1e3
            # M shape: (n_regions, n_munis), entries area(intersect)/area(muni)
            M = atlite.cutout.compute_indicatormatrix(sub.geometry, regions_3035)
            # Diagnostic: per-muni share of area covered by the union of regions
            # (1.0 = fully covered; <1.0 means part of the muni lies outside
            # regions_onshore, e.g., on small coastal slivers).
            coverage = np.asarray(M.sum(axis=0)).flatten()
            lost_kinhab = float(((1.0 - coverage) * pop_kinhab).sum())
            if lost_kinhab > 1e-3:
                logger.warning(
                    "########## PyPSA-Spain [build_clustered_population_layouts]: "
                    f"{label}: {lost_kinhab * 1e3:.0f} inhabitants outside "
                    f"regions_onshore (mean muni coverage = {coverage.mean() * 100:.2f}%)"
                )
            return pd.Series(
                np.asarray(M.dot(pop_kinhab)).flatten(),
                index=clustered_regions.index,
                name=label,
            )

        urban = _aggregate(urban_ids, "urban")
        rural = _aggregate(rural_ids, "rural")

        pop = pd.concat([urban, rural], axis=1)
        pop["total"] = pop["urban"] + pop["rural"]
        pop = pop[["total", "urban", "rural"]]

        pop["ct"] = pop.index.str[:2]
        country_population = pop.total.groupby(pop.ct).sum()
        pop["fraction"] = pop.total / pop.ct.map(country_population)

        pop.to_csv(snakemake.output.clustered_pop_layout)

        logger.info(
            "########## PyPSA-Spain [build_clustered_population_layouts]: "
            f"total={pop.total.sum():.1f} kinhab, "
            f"urban={pop.urban.sum():.1f} ({100 * pop.urban.sum() / pop.total.sum():.1f}%), "
            f"rural={pop.rural.sum():.1f} ({100 * pop.rural.sum() / pop.total.sum():.1f}%)"
        )
    #
    #
    ######################
