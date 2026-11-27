# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Aggregate gridded population layouts to clustered model regions.

The total, urban and rural population layouts on the cutout grid are summed
over each onshore region using the cutout indicator matrix. Each grid cell's
population is split among the regions it overlaps, so that the population of
coastal cells is not lost to the sea. The output table
also holds each region's country and its share of the national population,
which later rules use to distribute national demands and capacities to
regions.
"""

import logging

import atlite   ##### Required in PyPSA-Spain
import geopandas as gpd
import numpy as np   ##### Required in PyPSA-Spain
import pandas as pd
import scipy.sparse as sps   ##### Required in PyPSA-Spain
import xarray as xr
from sklearn.preprocessing import normalize

from scripts._helpers import configure_logging, load_cutout, set_scenario_config

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_clustered_population_layouts")

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    cutout = load_cutout(snakemake.input.cutout)

    clustered_regions = (
        gpd.read_file(snakemake.input.onshore_regions).set_index("name").buffer(0)
    )

    I = cutout.indicatormatrix(clustered_regions)  # noqa: E741
    I = normalize(I, norm="l1", axis=0)  # noqa: E741

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
    # Rationale: the cell-based aggregation above splits each cell's population
    # among the regions it overlaps in proportion to area, i.e. it assumes
    # population is uniformly distributed within each cell, so its resolution is
    # limited by the cutout grid. The municipality-level aggregation below keeps
    # the municipal resolution instead.
    #
    # Here we skip the cell intermediation: build_population_layouts.py exports
    # two CSVs (urban / rural codmun_ine + nombre + ccaa + pob_23), and we
    # combine them with the original municipality shapefile to perform an
    # area-weighted aggregation per clustered region using atlite's indicator
    # matrix:
    #
    #     M[r, m] = area(intersect(region_r, muni_m)) / area(muni_m)
    #     M[r, m] <- M[r, m] / sum_r M[r, m]   (normalised per municipality)
    #     pop_region = M @ pop_muni
    #
    # Each municipality's population is split across the regions that
    # intersect it in proportion to the overlapped area. The normalisation
    # keeps the whole population of municipalities only partly covered by
    # regions_onshore (coastal cities), and municipalities not covered at all
    # go to the nearest region, so the total population is preserved exactly.
    #
    pop_layouts_HR = snakemake.params.pop_layouts_HR
    if pop_layouts_HR.get("enable", False):
        logger.info(
            "##### [PyPSA-Spain] <build_clustered_population_layouts.py>: "
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
            # atlite looks up these shapes by position, not by index label
            M = atlite.cutout.compute_indicatormatrix(list(sub.geometry), regions_3035)
            M = sps.csc_matrix(M)
            # Per-muni share of area covered by the union of regions (1.0 = fully
            # covered; <1.0 means part of the muni lies outside regions_onshore, whose
            # simplified coastline leaves out part of many coastal municipalities).
            coverage = np.asarray(M.sum(axis=0)).flatten()
            # Normalise each muni over the regions it overlaps, so that its whole
            # population is split among them (the municipal analogue of PyPSA-Eur
            # #2459, which normalises the cell-based indicator matrix). Without it,
            # ~1% of the population was lost, mostly in coastal cities.
            covered = coverage > 0
            M = M @ sps.diags(np.where(covered, 1.0 / np.where(covered, coverage, 1.0), 0.0))
            logger.info(
                "##### [PyPSA-Spain] <build_clustered_population_layouts.py>: "
                f"{label}: {((1.0 - coverage[covered]) * pop_kinhab[covered]).sum() * 1e3:.0f} "
                f"inhabitants of partly covered municipalities reassigned to the regions "
                f"they overlap (mean muni coverage = {coverage.mean() * 100:.2f}%)"
            )
            pop_region = np.asarray(M.dot(pop_kinhab)).flatten()
            # Municipalities not overlapping any region (e.g. Formentera, absent from
            # the simplified shapes) go to the nearest region.
            if (~covered).any():
                centroids = sub.geometry[~covered].centroid
                nearest = np.array(
                    [regions_3035.distance(c).to_numpy().argmin() for c in centroids]
                )
                np.add.at(pop_region, nearest, pop_kinhab[~covered])
                logger.warning(
                    "##### [PyPSA-Spain] <build_clustered_population_layouts.py>: "
                    f"{label}: {(~covered).sum()} municipalities "
                    f"({pop_kinhab[~covered].sum() * 1e3:.0f} inhabitants) outside "
                    f"regions_onshore assigned to the nearest region: "
                    f"{list(sub.index[~covered])}"
                )
            return pd.Series(pop_region, index=clustered_regions.index, name=label)

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
            "##### [PyPSA-Spain] <build_clustered_population_layouts.py>: "
            f"total={pop.total.sum():.1f} kinhab, "
            f"urban={pop.urban.sum():.1f} ({100 * pop.urban.sum() / pop.total.sum():.1f}%), "
            f"rural={pop.rural.sum():.1f} ({100 * pop.rural.sum() / pop.total.sum():.1f}%)"
        )
    #
    #
    ######################
