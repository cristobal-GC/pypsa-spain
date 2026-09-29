# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Build solar rooftop potentials for all clustered model regions per resource class.
"""

import geopandas as gpd
import pandas as pd
import xarray as xr

from scripts._helpers import load_cutout, set_scenario_config

######################################## PyPSA-Spain
#
import logging

from scripts._helpers import configure_logging

logger = logging.getLogger(__name__)


# Rooftop PV capacity per person for each clustered region.
#
# The approach consists in, for each clustered region, averaging the kW/person values of the
# NUTS2 regions it overlaps, weighted by the overlapped area.

##### Scenario name in config -> column in the kW/person file
##### (roof_fraction: the full roof value is scaled by the roof_fraction parameter)
SCENARIO_COLUMNS = {
    "without_surplus_remuneration": "kw_per_person_opt_no_surplus_remuneration",
    "with_surplus_remuneration": "kw_per_person_opt_surplus_remuneration",
    "roof_fraction": "kw_per_person_full_roof",
}


def fun_kw_per_person_by_region(regions, nuts2_ES, df_kw, column):

    ##### Add kW/person to nuts2_ES
    nuts2_ES = nuts2_ES.join(df_kw[column].rename("kw_per_person"), how="inner")

    ##### Make intersection, and weight by intersected area
    intersected = gpd.overlay(
        nuts2_ES[["kw_per_person", "geometry"]],
        regions.rename_axis("name").reset_index()[["name", "geometry"]],
        how="intersection",
    )
    intersected["area_intersection"] = intersected.geometry.area
    area = intersected.groupby("name")["area_intersection"].sum()
    intersected["weight"] = intersected["area_intersection"] / intersected["name"].map(area)

    ##### Regions not overlapping any NUTS2 region are not included
    return (intersected["weight"] * intersected["kw_per_person"]).groupby(
        intersected["name"]
    ).sum()


def plot_kw_per_person(regions, kw_per_person, nuts2_ES, title, fn):

    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.pyplot as plt

    # map extent in lon/lat: [lon_min, lon_max, lat_min, lat_max]
    # (Iberian Peninsula and Balearic Islands, Canary Islands are omitted)
    map_bounds = (-9.93, 4.5, 35.61, 44.18)

    # plot in lon/lat (EPSG:4326), only regions within the map extent
    regions_plot = regions.to_crs(4326).assign(kw_per_person=kw_per_person)
    regions_plot = regions_plot.cx[
        map_bounds[0] : map_bounds[1], map_bounds[2] : map_bounds[3]
    ]

    # figsize chosen close to the data bbox aspect ratio to minimise margins
    bbox_ratio = (map_bounds[1] - map_bounds[0]) / (map_bounds[3] - map_bounds[2])
    fig, ax = plt.subplots(figsize=(7 * bbox_ratio + 1.0, 7))
    regions_plot.plot(
        ax=ax,
        column="kw_per_person",
        cmap="YlOrRd",
        legend=True,
        legend_kwds={"label": "Rooftop PV capacity (kW/person)", "shrink": 0.8},
        edgecolor="white",
        linewidth=0.3,
    )
    if nuts2_ES is not None:
        # Ceuta (ES63), Melilla (ES64) and Canary Islands (ES70) are omitted
        nuts2_ES.drop(["ES63", "ES64", "ES70"], errors="ignore").to_crs(
            4326
        ).boundary.plot(ax=ax, color="grey", linewidth=0.4)
    ax.set_xlim(map_bounds[0], map_bounds[1])
    ax.set_ylim(map_bounds[2], map_bounds[3])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.01)
    fig.savefig(fn, dpi=150, bbox_inches="tight")
    plt.close(fig)


#
########################################


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake(
            "build_solar_rooftop_potentials",
            configfiles="config/test/config.myopic.yaml",
        )

    configure_logging(snakemake)  ##### PyPSA-Spain
    set_scenario_config(snakemake)

    cutout = load_cutout(snakemake.input.cutout)

    class_regions = gpd.read_file(snakemake.input.class_regions).set_index(
        ["bus", "bin"]
    )

    I = cutout.indicatormatrix(class_regions)  # noqa: E741

    with xr.open_dataarray(snakemake.input.pop_layout) as pop_layout:
        pop = I.dot(pop_layout.stack(spatial=("y", "x")))

    # add max solar rooftop potential assuming 0.1 kW/m2 and 20 m2/person,
    # i.e. 2 kW/person (population data is in thousands of people) so we get MW
    potentials = 0.1 * 20 * pd.Series(pop, index=class_regions.index)

    ######################################## PyPSA-Spain
    #
    # Overwrite the 2 kW/person of PyPSA-Eur with a kW/person value for each clustered region,
    # obtained from NUTS2 values weighted by overlapped area. Regions not overlapping any NUTS2
    # region keep the PyPSA-Eur value.
    solar_rooftop = snakemake.params.solar_rooftop
    regions = gpd.read_file(snakemake.input.onshore_regions).set_index("name").to_crs(3035)
    kw_per_person = pd.Series(0.1 * 20, index=regions.index)
    nuts2_ES = None
    scenario = "PyPSA-Eur default"

    if solar_rooftop["enable"]:
        scenario = solar_rooftop["scenario"]
        if scenario not in SCENARIO_COLUMNS:
            raise ValueError(
                f"Unknown solar_rooftop scenario '{scenario}'. Valid options: {list(SCENARIO_COLUMNS)}"
            )
        column = SCENARIO_COLUMNS[scenario]

        df_kw = pd.read_csv(snakemake.input.kw_per_person, index_col="nuts2")

        if scenario == "roof_fraction":
            roof_fraction = solar_rooftop["roof_fraction"]
            if not 0 < roof_fraction <= 1:
                raise ValueError(
                    f"solar_rooftop roof_fraction must be in (0, 1], got {roof_fraction}"
                )
            df_kw[column] *= roof_fraction
            scenario = f"roof_fraction = {roof_fraction:.2f}"

        nuts2_ES = gpd.read_file(snakemake.input.nuts2_ES).set_index("id").to_crs(3035)

        kw_per_person_nuts2 = fun_kw_per_person_by_region(
            regions, nuts2_ES, df_kw, column
        )
        kw_per_person.update(kw_per_person_nuts2)

        buses = potentials.index.get_level_values("bus")
        potentials = potentials / (0.1 * 20) * kw_per_person.reindex(buses, fill_value=0.1 * 20).values

        for bb, vv in kw_per_person.items():
            logger.info(
                f"########## [PyPSA-Spain] <build_solar_rooftop_potentials.py> Region {bb}: {vv:.2f} kW/person"
            )

    logger.info(
        f"########## [PyPSA-Spain] <build_solar_rooftop_potentials.py> Solar rooftop potential ({scenario}): {potentials.sum() / 1e3:.1f} GW"
    )

    title = (
        f"Rooftop PV capacity per person ({scenario})\n"
        f"Range: {kw_per_person.min():.2f}-{kw_per_person.max():.2f} kW/person, "
        f"total potential: {potentials.sum() / 1e3:.1f} GW"
    )
    plot_kw_per_person(
        regions, kw_per_person, nuts2_ES, title, snakemake.output.map_kw_per_person
    )
    #
    ########################################

    potentials.to_csv(snakemake.output.potentials)
