# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2017-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Adds electrical generators and existing hydro storage units to a base network.

Relevant Settings
-----------------

.. code:: yaml

    costs:
        year:
        version:
        dicountrate:
        emission_prices:

    electricity:
        max_hours:
        marginal_cost:
        capital_cost:
        conventional_carriers:
        co2limit:
        extendable_carriers:
        estimate_renewable_capacities:


    load:
        scaling_factor:

    renewable:
        hydro:
            carriers:
            hydro_max_hours:
            hydro_capital_cost:

    lines:
        length_factor:

.. seealso::
    Documentation of the configuration file ``config/config.yaml`` at :ref:`costs_cf`,
    :ref:`electricity_cf`, :ref:`load_cf`, :ref:`renewable_cf`, :ref:`lines_cf`

Inputs
------

- ``resources/costs.csv``: The database of cost assumptions for all included technologies for specific years from various sources; e.g. discount rate, lifetime, investment (CAPEX), fixed operation and maintenance (FOM), variable operation and maintenance (VOM), fuel costs, efficiency, carbon-dioxide intensity.
- ``data/hydro_capacities.csv``: Hydropower plant store/discharge power capacities, energy storage capacity, and average hourly inflow by country.

    .. image:: img/hydrocapacities.png
        :scale: 34 %

- ``resources/electricity_demand.csv`` Hourly per-country electricity demand profiles.
- ``resources/regions_onshore.geojson``: confer :ref:`busregions`
- ``resources/nuts3_shapes.geojson``: confer :ref:`shapes`
- ``resources/powerplants.csv``: confer :ref:`powerplants`
- ``resources/profile_{}.nc``: all technologies in ``config["renewables"].keys()``, confer :ref:`renewableprofiles`.
- ``networks/base.nc``: confer :ref:`base`

Outputs
-------

- ``networks/elec.nc``:

    .. image:: img/elec.png
            :scale: 33 %

Description
-----------

The rule :mod:`add_electricity` ties all the different data inputs from the preceding rules together into a detailed PyPSA network that is stored in ``networks/elec.nc``. It includes:

- today's transmission topology and transfer capacities (optionally including lines which are under construction according to the config settings ``lines: under_construction`` and ``links: under_construction``),
- today's thermal and hydro power generation capacities (for the technologies listed in the config setting ``electricity: conventional_carriers``), and
- today's load time-series (upsampled in a top-down approach according to population and gross domestic product)

It further adds extendable ``generators`` with **zero** capacity for

- photovoltaic, onshore and AC- as well as DC-connected offshore wind installations with today's locational, hourly wind and solar capacity factors (but **no** current capacities),
- additional open- and combined-cycle gas turbines (if ``OCGT`` and/or ``CCGT`` is listed in the config setting ``electricity: extendable_carriers``)
"""

import logging
from itertools import product
from pathlib import Path
from typing import Dict, List

import geopandas as gpd
import numpy as np
import pandas as pd
import powerplantmatching as pm
import pypsa
import scipy.sparse as sparse
import xarray as xr
import yaml   ##### Required in PyPSA-Spain
from _helpers import (
    configure_logging,
    get_snapshots,
    set_scenario_config,
    update_p_nom_max,
)
from powerplantmatching.export import map_country_bus
from shapely.prepared import prep

idx = pd.IndexSlice

logger = logging.getLogger(__name__)


def normed(s):
    return s / s.sum()


def calculate_annuity(n, r):
    """
    Calculate the annuity factor for an asset with lifetime n years and.

    discount rate of r, e.g. annuity(20, 0.05) * 20 = 1.6
    """
    if isinstance(r, pd.Series):
        return pd.Series(1 / n, index=r.index).where(
            r == 0, r / (1.0 - 1.0 / (1.0 + r) ** n)
        )
    elif r > 0:
        return r / (1.0 - 1.0 / (1.0 + r) ** n)
    else:
        return 1 / n


def add_missing_carriers(n, carriers):
    """
    Function to add missing carriers to the network without raising errors.
    """
    missing_carriers = set(carriers) - set(n.carriers.index)
    if len(missing_carriers) > 0:
        n.madd("Carrier", missing_carriers)


def sanitize_carriers(n, config):
    """
    Sanitize the carrier information in a PyPSA Network object.

    The function ensures that all unique carrier names are present in the network's
    carriers attribute, and adds nice names and colors for each carrier according
    to the provided configuration dictionary.

    Parameters
    ----------
    n : pypsa.Network
        A PyPSA Network object that represents an electrical power system.
    config : dict
        A dictionary containing configuration information, specifically the
        "plotting" key with "nice_names" and "tech_colors" keys for carriers.

    Returns
    -------
    None
        The function modifies the 'n' PyPSA Network object in-place, updating the
        carriers attribute with nice names and colors.

    Warnings
    --------
    Raises a warning if any carrier's "tech_colors" are not defined in the config dictionary.
    """

    for c in n.iterate_components():
        if "carrier" in c.df:
            add_missing_carriers(n, c.df.carrier)

    carrier_i = n.carriers.index
    nice_names = (
        pd.Series(config["plotting"]["nice_names"])
        .reindex(carrier_i)
        .fillna(carrier_i.to_series())
    )
    n.carriers["nice_name"] = n.carriers.nice_name.where(
        n.carriers.nice_name != "", nice_names
    )
    colors = pd.Series(config["plotting"]["tech_colors"]).reindex(carrier_i)
    if colors.isna().any():
        missing_i = list(colors.index[colors.isna()])
        logger.warning(f"tech_colors for carriers {missing_i} not defined in config.")
    n.carriers["color"] = n.carriers.color.where(n.carriers.color != "", colors)


def sanitize_locations(n):
    if "location" in n.buses.columns:
        n.buses["x"] = n.buses.x.where(n.buses.x != 0, n.buses.location.map(n.buses.x))
        n.buses["y"] = n.buses.y.where(n.buses.y != 0, n.buses.location.map(n.buses.y))
        n.buses["country"] = n.buses.country.where(
            n.buses.country.ne("") & n.buses.country.notnull(),
            n.buses.location.map(n.buses.country),
        )


def add_co2_emissions(n, costs, carriers):
    """
    Add CO2 emissions to the network's carriers attribute.
    """
    suptechs = n.carriers.loc[carriers].index.str.split("-").str[0]
    n.carriers.loc[carriers, "co2_emissions"] = costs.co2_emissions[suptechs].values


def load_costs(tech_costs, config, max_hours, Nyears=1.0):
    # set all asset costs and other parameters
    costs = pd.read_csv(tech_costs, index_col=[0, 1]).sort_index()

    # correct units to MW
    costs.loc[costs.unit.str.contains("/kW"), "value"] *= 1e3
    costs.unit = costs.unit.str.replace("/kW", "/MW")

    fill_values = config["fill_values"]
    costs = costs.value.unstack().fillna(fill_values)

    costs["capital_cost"] = (
        (
            calculate_annuity(costs["lifetime"], costs["discount rate"])
            + costs["FOM"] / 100.0
        )
        * costs["investment"]
        * Nyears
    )
    costs.at["OCGT", "fuel"] = costs.at["gas", "fuel"]
    costs.at["CCGT", "fuel"] = costs.at["gas", "fuel"]

    costs["marginal_cost"] = costs["VOM"] + costs["fuel"] / costs["efficiency"]

    costs = costs.rename(columns={"CO2 intensity": "co2_emissions"})

    costs.at["OCGT", "co2_emissions"] = costs.at["gas", "co2_emissions"]
    costs.at["CCGT", "co2_emissions"] = costs.at["gas", "co2_emissions"]

    costs.at["solar", "capital_cost"] = costs.at["solar-utility", "capital_cost"]

    costs = costs.rename({"solar-utility single-axis tracking": "solar-hsat"})

    def costs_for_storage(store, link1, link2=None, max_hours=1.0):
        capital_cost = link1["capital_cost"] + max_hours * store["capital_cost"]
        if link2 is not None:
            capital_cost += link2["capital_cost"]
        return pd.Series(
            dict(capital_cost=capital_cost, marginal_cost=0.0, co2_emissions=0.0)
        )

    costs.loc["battery"] = costs_for_storage(
        costs.loc["battery storage"],
        costs.loc["battery inverter"],
        max_hours=max_hours["battery"],
    )
    costs.loc["H2"] = costs_for_storage(
        costs.loc["hydrogen storage underground"],
        costs.loc["fuel cell"],
        costs.loc["electrolysis"],
        max_hours=max_hours["H2"],
    )

    for attr in ("marginal_cost", "capital_cost"):
        overwrites = config.get(attr)
        if overwrites is not None:
            overwrites = pd.Series(overwrites)
            costs.loc[overwrites.index, attr] = overwrites

    return costs


def load_powerplants(ppl_fn):
    carrier_dict = {
        "ocgt": "OCGT",
        "ccgt": "CCGT",
        "bioenergy": "biomass",
        "ccgt, thermal": "CCGT",
        "hard coal": "coal",
    }
    return (
        pd.read_csv(ppl_fn, index_col=0, dtype={"bus": "str"})
        .powerplant.to_pypsa_names()
        .rename(columns=str.lower)
        .replace({"carrier": carrier_dict})
    )


def shapes_to_shapes(orig, dest):
    """
    Adopted from vresutils.transfer.Shapes2Shapes()
    """
    orig_prepped = list(map(prep, orig))
    transfer = sparse.lil_matrix((len(dest), len(orig)), dtype=float)

    for i, j in product(range(len(dest)), range(len(orig))):
        if orig_prepped[j].intersects(dest.iloc[i]):
            area = orig.iloc[j].intersection(dest.iloc[i]).area
            transfer[i, j] = area / dest.iloc[i].area

    return transfer


def attach_load(
    n, regions, load, nuts3_shapes, gdp_pop_non_nuts3, countries, scaling=1.0
):

    substation_lv_i = n.buses.index[n.buses["substation_lv"]]

    gdf_regions = gpd.read_file(regions).set_index("name").reindex(substation_lv_i)
    opsd_load = pd.read_csv(load, index_col=0, parse_dates=True).filter(items=countries)

    logger.info(f"Load data scaled by factor {scaling}.")
    opsd_load *= scaling

    nuts3 = gpd.read_file(nuts3_shapes).set_index("index")

    def upsample(cntry, group, gdp_pop_non_nuts3):
        load = opsd_load[cntry]

        if len(group) == 1:
            return pd.DataFrame({group.index[0]: load})
        nuts3_cntry = nuts3.loc[nuts3.country == cntry]
        transfer = shapes_to_shapes(group, nuts3_cntry.geometry).T.tocsr()
        gdp_n = pd.Series(
            transfer.dot(nuts3_cntry["gdp"].fillna(1.0).values), index=group.index
        )
        pop_n = pd.Series(
            transfer.dot(nuts3_cntry["pop"].fillna(1.0).values), index=group.index
        )


        ######################################## PyPSA-Spain

        ##### Use specific 'pop' and 'gdp' coefficients computed for ES
        # 'normed' makes the elements of a vector to sum 1
        if update_gdp_pop and cntry=='ES':
            print(f'##### [PyPSA-Spain]: Using updated "gdp" and "pop" coefficients for ES ..')
            ##### New                
            factors = normed(0.18 * normed(gdp_n) + 0.82 * normed(pop_n))   
        else:
            ##### Original
            # relative factors 0.6 and 0.4 have been determined from a linear
            # regression on the country to continent load data
            factors = normed(0.6 * normed(gdp_n) + 0.4 * normed(pop_n))   

        ########################################


        if cntry in ["UA", "MD"]:
            # overwrite factor because nuts3 provides no data for UA+MD
            gdp_pop_non_nuts3 = gpd.read_file(gdp_pop_non_nuts3).set_index("Bus")
            gdp_pop_non_nuts3 = gdp_pop_non_nuts3.loc[
                (gdp_pop_non_nuts3.country == cntry)
                & (gdp_pop_non_nuts3.index.isin(substation_lv_i))
            ]
            factors = normed(
                0.6 * normed(gdp_pop_non_nuts3["gdp"])
                + 0.4 * normed(gdp_pop_non_nuts3["pop"])
            )
        return pd.DataFrame(
            factors.values * load.values[:, np.newaxis],
            index=load.index,
            columns=factors.index,
        )

    load = pd.concat(
        [
            upsample(cntry, group, gdp_pop_non_nuts3)
            for cntry, group in gdf_regions.geometry.groupby(gdf_regions.country)
        ],
        axis=1,
    )

    n.madd(
        "Load", substation_lv_i, bus=substation_lv_i, p_set=load
    )  # carrier="electricity"





######################################## PyPSA-Spain
#
# Function to attach loads according to PyPSA-Spain methodology
#

def attach_load_vPyPSA_Spain(
    n, regions, load, nuts3_shapes, countries, scaling=1.0 # remove 'gdp_pop_non_nuts3' from inputs
):
    """
    ########## This version is to:
    # - Use a specific load time series for each region (community NUTS2 or province NUTS3), rather than using the load profile at national level.
    # - The regions are named through the NUTS ID, see columns in 'resources/electricity_demand.csv'
    """

    substation_lv_i = n.buses.index[n.buses["substation_lv"]] 

    
    ##### Creates a gdf with index=name, and filters the 522 buses defined above
    # Columns are: x, y, country, geometry
    gdf_regions = gpd.read_file(regions).set_index("name").reindex(substation_lv_i)
    
    ##### Original: read country load time series
    # opsd_load = pd.read_csv(load, index_col=0, parse_dates=True).filter(items=countries)
    ##### New: read region load time series (country filter removed)
    opsd_load = pd.read_csv(load, index_col=0, parse_dates=True)
    logger.info(f"Load data scaled by factor {scaling}.")
    opsd_load *= scaling

    ########## Creates gdf with index=CCnnn (NUTS3 code)
    # Columns are: pop, gdp, country, geometry
    nuts3 = gpd.read_file(nuts3_shapes).set_index("index")

    ########## This function will be called with a .groupby, so that:
    # cntry: is a country within regions.country (here, only 'ES')
    # group: is one of the (522) geometries within regions.geometry of the country cntry (ES)
    #        with bus number as index
    def upsample(cntry, group):##### , gdp_pop_non_nuts3):

        ##### Original
        # load = opsd_load[cntry]
        # if len(group) == 1:
        #     return pd.DataFrame({group.index[0]: load})        
        ##### New: define where to add return_rr (with the load time series of each substation within rr)
        return_total = pd.DataFrame(0, index = opsd_load.index, columns = group.index) 

        for rr_ID in opsd_load.columns:  ##### rr_ID is the NUTS ID

            load = opsd_load[rr_ID]


            ##### Original: take NUTS3 in country
            # nuts3_cntry = nuts3.loc[nuts3.country == cntry]
            ##### New:
            nuts3_rr = nuts3[nuts3.index.str.contains(rr_ID)]   
        
            ##### Get a matrix with area overlap percentages
            #  rows are ~522 substations, columns are NUTS3 in región 
            # .tocsr() is to put the sparse matrix in 'Compressed Sparse Row' format.            
            transfer = shapes_to_shapes(group, nuts3_rr.geometry).T.tocsr()   

            ##### Series with substations, (overlapped area) x (gdp) from corresponding NUTS3 of rr
            gdp_n = pd.Series(
                transfer.dot(nuts3_rr["gdp"].fillna(1.0).values), index=group.index
            )

            ##### Series with substations, (overlapped area) x (pop) from corresponding NUTS3 of rr
            pop_n = pd.Series(
                transfer.dot(nuts3_rr["pop"].fillna(1.0).values), index=group.index
            )

            
            ##### Use specific 'pop' and 'gdp' coefficients computed for ES
            # 'normed' makes the elements of a vector to sum 1
            if update_gdp_pop:
                print(f'##### [PyPSA-Spain]: Using updated "gdp" and "pop" coefficients for ES ..')
                ##### New                
                factors = normed(0.18 * normed(gdp_n) + 0.82 * normed(pop_n))   
            else:
                ##### Original
                # relative factors 0.6 and 0.4 have been determined from a linear
                # regression on the country to continent load data
                factors = normed(0.6 * normed(gdp_n) + 0.4 * normed(pop_n))   


            ##### Remove for Ukraine and Moldavia 
            # if cntry in ["UA", "MD"]:
            #     # overwrite factor because nuts3 provides no data for UA+MD
            #     gdp_pop_non_nuts3 = gpd.read_file(gdp_pop_non_nuts3).set_index("Bus")
            #     gdp_pop_non_nuts3 = gdp_pop_non_nuts3.loc[
            #         (gdp_pop_non_nuts3.country == cntry)
            #         & (gdp_pop_non_nuts3.index.isin(substation_lv_i))
            #     ]
            #     factors = normed(
            #         0.6 * normed(gdp_pop_non_nuts3["gdp"])
            #         + 0.4 * normed(gdp_pop_non_nuts3["pop"])
            #     )

            ##### Original: return df
            # return pd.DataFrame(
            #     factors.values * load.values[:, np.newaxis],
            #     index=load.index,
            #     columns=factors.index,
            # )
            ##### New: add to return_total
            return_rr = pd.DataFrame(
                factors.values * load.values[:, np.newaxis], 
                index=load.index,
                columns=factors.index,
            )

            return_total += return_rr

        return return_total
            

    load = pd.concat(
        [
            upsample(cntry, group)#####, gdp_pop_non_nuts3)
            for cntry, group in gdf_regions.geometry.groupby(gdf_regions.country)
        ],
        axis=1,
    )

    n.madd(
        "Load", substation_lv_i, bus=substation_lv_i, p_set=load
    )  # carrier="electricity"
#
#
#
########################################





def update_transmission_costs(n, costs, length_factor=1.0):
    # TODO: line length factor of lines is applied to lines and links.
    # Separate the function to distinguish.

    n.lines["capital_cost"] = (
        n.lines["length"] * length_factor * costs.at["HVAC overhead", "capital_cost"]
    )

    if n.links.empty:
        return

    dc_b = n.links.carrier == "DC"

    # If there are no dc links, then the 'underwater_fraction' column
    # may be missing. Therefore we have to return here.
    if n.links.loc[dc_b].empty:
        return

    costs = (
        n.links.loc[dc_b, "length"]
        * length_factor
        * (
            (1.0 - n.links.loc[dc_b, "underwater_fraction"])
            * costs.at["HVDC overhead", "capital_cost"]
            + n.links.loc[dc_b, "underwater_fraction"]
            * costs.at["HVDC submarine", "capital_cost"]
        )
        + costs.at["HVDC inverter pair", "capital_cost"]
    )
    n.links.loc[dc_b, "capital_cost"] = costs


def attach_wind_and_solar(
    n, costs, input_profiles, carriers, extendable_carriers, line_length_factor=1
):
    add_missing_carriers(n, carriers)
    for car in carriers:
        if car == "hydro":
            continue

        with xr.open_dataset(getattr(input_profiles, "profile_" + car)) as ds:
            if ds.indexes["bus"].empty:
                continue

            # if-statement for compatibility with old profiles
            if "year" in ds.indexes:
                ds = ds.sel(year=ds.year.min(), drop=True)

            supcar = car.split("-", 2)[0]
            if supcar == "offwind":
                underwater_fraction = ds["underwater_fraction"].to_pandas()
                connection_cost = (
                    line_length_factor
                    * ds["average_distance"].to_pandas()
                    * (
                        underwater_fraction
                        * costs.at[car + "-connection-submarine", "capital_cost"]
                        + (1.0 - underwater_fraction)
                        * costs.at[car + "-connection-underground", "capital_cost"]
                    )
                )
                capital_cost = (
                    costs.at["offwind", "capital_cost"]
                    + costs.at[car + "-station", "capital_cost"]
                    + connection_cost
                )
                logger.info(
                    "Added connection cost of {:0.0f}-{:0.0f} Eur/MW/a to {}".format(
                        connection_cost.min(), connection_cost.max(), car
                    )
                )
            else:
                capital_cost = costs.at[car, "capital_cost"]

            n.madd(
                "Generator",
                ds.indexes["bus"],
                " " + car,
                bus=ds.indexes["bus"],
                carrier=car,
                p_nom_extendable=car in extendable_carriers["Generator"],
                p_nom_max=ds["p_nom_max"].to_pandas(),
                weight=ds["weight"].to_pandas(),
                marginal_cost=costs.at[supcar, "marginal_cost"],
                capital_cost=capital_cost,
                efficiency=costs.at[supcar, "efficiency"],
                p_max_pu=ds["profile"].transpose("time", "bus").to_pandas(),
                lifetime=costs.at[supcar, "lifetime"],
            )


def attach_conventional_generators(
    n,
    costs,
    ppl,
    conventional_carriers,
    extendable_carriers,
    conventional_params,
    conventional_inputs,
    unit_commitment=None,
    fuel_price=None,
):
    carriers = list(set(conventional_carriers) | set(extendable_carriers["Generator"]))

    # Replace carrier "natural gas" with the respective technology (OCGT or
    # CCGT) to align with PyPSA names of "carriers" and avoid filtering "natural
    # gas" powerplants in ppl.query("carrier in @carriers")
    ppl.loc[ppl["carrier"] == "natural gas", "carrier"] = ppl.loc[
        ppl["carrier"] == "natural gas", "technology"
    ]

    ppl = (
        ppl.query("carrier in @carriers")
        .join(costs, on="carrier", rsuffix="_r")
        .rename(index=lambda s: f"C{str(s)}")
    )
    ppl["efficiency"] = ppl.efficiency.fillna(ppl.efficiency_r)

    # reduce carriers to those in power plant dataset
    carriers = list(set(carriers) & set(ppl.carrier.unique()))
    add_missing_carriers(n, carriers)
    add_co2_emissions(n, costs, carriers)

    if unit_commitment is not None:
        committable_attrs = ppl.carrier.isin(unit_commitment).to_frame("committable")
        for attr in unit_commitment.index:
            default = pypsa.components.component_attrs["Generator"].default[attr]
            committable_attrs[attr] = ppl.carrier.map(unit_commitment.loc[attr]).fillna(
                default
            )
    else:
        committable_attrs = {}

    if fuel_price is not None:
        fuel_price = fuel_price.assign(
            OCGT=fuel_price["gas"], CCGT=fuel_price["gas"]
        ).drop("gas", axis=1)
        missing_carriers = list(set(carriers) - set(fuel_price))
        fuel_price = fuel_price.assign(**costs.fuel[missing_carriers])
        fuel_price = fuel_price.reindex(ppl.carrier, axis=1)
        fuel_price.columns = ppl.index
        marginal_cost = fuel_price.div(ppl.efficiency).add(ppl.carrier.map(costs.VOM))
    else:
        marginal_cost = (
            ppl.carrier.map(costs.VOM) + ppl.carrier.map(costs.fuel) / ppl.efficiency
        )

    # Define generators using modified ppl DataFrame
    caps = ppl.groupby("carrier").p_nom.sum().div(1e3).round(2)
    logger.info(f"Adding {len(ppl)} generators with capacities [GW] \n{caps}")

    n.madd(
        "Generator",
        ppl.index,
        carrier=ppl.carrier,
        bus=ppl.bus,
        p_nom_min=ppl.p_nom.where(ppl.carrier.isin(conventional_carriers), 0),
        p_nom=ppl.p_nom.where(ppl.carrier.isin(conventional_carriers), 0),
        p_nom_extendable=ppl.carrier.isin(extendable_carriers["Generator"]),
        efficiency=ppl.efficiency,
        marginal_cost=marginal_cost,
        capital_cost=ppl.capital_cost,
        build_year=ppl.datein.fillna(0).astype(int),
        lifetime=(ppl.dateout - ppl.datein).fillna(np.inf),
        **committable_attrs,
    )

    for carrier in set(conventional_params) & set(carriers):
        # Generators with technology affected
        idx = n.generators.query("carrier == @carrier").index

        for attr in list(set(conventional_params[carrier]) & set(n.generators)):
            values = conventional_params[carrier][attr]

            if f"conventional_{carrier}_{attr}" in conventional_inputs:
                # Values affecting generators of technology k country-specific
                # First map generator buses to countries; then map countries to p_max_pu
                values = pd.read_csv(
                    snakemake.input[f"conventional_{carrier}_{attr}"], index_col=0
                ).iloc[:, 0]
                bus_values = n.buses.country.map(values)
                n.generators.update(
                    {attr: n.generators.loc[idx].bus.map(bus_values).dropna()}
                )
            else:
                # Single value affecting all generators of technology k indiscriminantely of country
                n.generators.loc[idx, attr] = values


def attach_hydro(n, costs, ppl, profile_hydro, hydro_capacities, carriers, **params):
    add_missing_carriers(n, carriers)
    add_co2_emissions(n, costs, carriers)

    ppl = (
        ppl.query('carrier == "hydro"')
        .reset_index(drop=True)
        .rename(index=lambda s: f"{str(s)} hydro")
    )
    ror = ppl.query('technology == "Run-Of-River"')
    phs = ppl.query('technology == "Pumped Storage"')
    hydro = ppl.query('technology == "Reservoir"')

    country = ppl["bus"].map(n.buses.country).rename("country")

    inflow_idx = ror.index.union(hydro.index)
    if not inflow_idx.empty:
        dist_key = ppl.loc[inflow_idx, "p_nom"].groupby(country).transform(normed)

        with xr.open_dataarray(profile_hydro) as inflow:
            inflow_countries = pd.Index(country[inflow_idx])
            missing_c = inflow_countries.unique().difference(
                inflow.indexes["countries"]
            )
            assert missing_c.empty, (
                f"'{profile_hydro}' is missing "
                f"inflow time-series for at least one country: {', '.join(missing_c)}"
            )

            inflow_t = (
                inflow.sel(countries=inflow_countries)
                .rename({"countries": "name"})
                .assign_coords(name=inflow_idx)
                .transpose("time", "name")
                .to_pandas()
                .multiply(dist_key, axis=1)
            )

    if "ror" in carriers and not ror.empty:
        n.madd(
            "Generator",
            ror.index,
            carrier="ror",
            bus=ror["bus"],
            p_nom=ror["p_nom"],
            efficiency=costs.at["ror", "efficiency"],
            capital_cost=costs.at["ror", "capital_cost"],
            weight=ror["p_nom"],
            p_max_pu=(
                inflow_t[ror.index]
                .divide(ror["p_nom"], axis=1)
                .where(lambda df: df <= 1.0, other=1.0)
            ),
        )

    if "PHS" in carriers and not phs.empty:
        # fill missing max hours to params value and
        # assume no natural inflow due to lack of data
        max_hours = params.get("PHS_max_hours", 6)
        phs = phs.replace({"max_hours": {0: max_hours, np.nan: max_hours}})
        n.madd(
            "StorageUnit",
            phs.index,
            carrier="PHS",
            bus=phs["bus"],
            p_nom=phs["p_nom"],
            capital_cost=costs.at["PHS", "capital_cost"],
            max_hours=phs["max_hours"],
            efficiency_store=np.sqrt(costs.at["PHS", "efficiency"]),
            efficiency_dispatch=np.sqrt(costs.at["PHS", "efficiency"]),
            cyclic_state_of_charge=True,
        )

    if "hydro" in carriers and not hydro.empty:
        hydro_max_hours = params.get("hydro_max_hours")

        assert hydro_max_hours is not None, "No path for hydro capacities given."

        hydro_stats = pd.read_csv(
            hydro_capacities, comment="#", na_values="-", index_col=0
        )
        e_target = hydro_stats["E_store[TWh]"].clip(lower=0.2) * 1e6
        e_installed = hydro.eval("p_nom * max_hours").groupby(hydro.country).sum()
        e_missing = e_target - e_installed
        missing_mh_i = hydro.query("max_hours.isnull()").index

        if hydro_max_hours == "energy_capacity_totals_by_country":
            # watch out some p_nom values like IE's are totally underrepresented
            max_hours_country = (
                e_missing / hydro.loc[missing_mh_i].groupby("country").p_nom.sum()
            )

        elif hydro_max_hours == "estimate_by_large_installations":
            max_hours_country = (
                hydro_stats["E_store[TWh]"] * 1e3 / hydro_stats["p_nom_discharge[GW]"]
            )

        max_hours_country.clip(0, inplace=True)

        missing_countries = pd.Index(hydro["country"].unique()).difference(
            max_hours_country.dropna().index
        )
        if not missing_countries.empty:
            logger.warning(
                f'Assuming max_hours=6 for hydro reservoirs in the countries: {", ".join(missing_countries)}'
            )
        hydro_max_hours = hydro.max_hours.where(
            hydro.max_hours > 0, hydro.country.map(max_hours_country)
        ).fillna(6)

        if params.get("flatten_dispatch", False):
            buffer = params.get("flatten_dispatch_buffer", 0.2)
            average_capacity_factor = inflow_t[hydro.index].mean() / hydro["p_nom"]
            p_max_pu = (average_capacity_factor + buffer).clip(upper=1)
        else:
            p_max_pu = 1

        n.madd(
            "StorageUnit",
            hydro.index,
            carrier="hydro",
            bus=hydro["bus"],
            p_nom=hydro["p_nom"],
            max_hours=hydro_max_hours,
            capital_cost=costs.at["hydro", "capital_cost"],
            marginal_cost=costs.at["hydro", "marginal_cost"],
            p_max_pu=p_max_pu,  # dispatch
            p_min_pu=0.0,  # store
            efficiency_dispatch=costs.at["hydro", "efficiency"],
            efficiency_store=0.0,
            cyclic_state_of_charge=True,
            inflow=inflow_t.loc[:, hydro.index],
        )


def attach_OPSD_renewables(n: pypsa.Network, tech_map: Dict[str, List[str]]) -> None:
    """
    Attach renewable capacities from the OPSD dataset to the network.

    Args:
    - n: The PyPSA network to attach the capacities to.
    - tech_map: A dictionary mapping fuel types to carrier names.

    Returns:
    - None
    """
    tech_string = ", ".join(sum(tech_map.values(), []))
    logger.info(f"Using OPSD renewable capacities for carriers {tech_string}.")

    df = pm.data.OPSD_VRE().powerplant.convert_country_to_alpha2()
    technology_b = ~df.Technology.isin(["Onshore", "Offshore"])
    df["Fueltype"] = df.Fueltype.where(technology_b, df.Technology).replace(
        {"Solar": "PV"}
    )
    df = df.query("Fueltype in @tech_map").powerplant.convert_country_to_alpha2()
    df = df.dropna(subset=["lat", "lon"])

    for fueltype, carriers in tech_map.items():
        gens = n.generators[lambda df: df.carrier.isin(carriers)]
        buses = n.buses.loc[gens.bus.unique()]
        gens_per_bus = gens.groupby("bus").p_nom.count()

        caps = map_country_bus(df.query("Fueltype == @fueltype"), buses)
        caps = caps.groupby(["bus"]).Capacity.sum()
        caps = caps / gens_per_bus.reindex(caps.index, fill_value=1)

        n.generators.update({"p_nom": gens.bus.map(caps).dropna()})
        n.generators.update({"p_nom_min": gens.bus.map(caps).dropna()})


def estimate_renewable_capacities(
    n: pypsa.Network, year: int, tech_map: dict, expansion_limit: bool, countries: list
) -> None:
    """
    Estimate a different between renewable capacities in the network and
    reported country totals from IRENASTAT dataset. Distribute the difference
    with a heuristic.

    Heuristic: n.generators_t.p_max_pu.mean() * n.generators.p_nom_max

    Args:
    - n: The PyPSA network.
    - year: The year of optimisation.
    - tech_map: A dictionary mapping fuel types to carrier names.
    - expansion_limit: Boolean value from config file
    - countries: A list of country codes to estimate capacities for.

    Returns:
    - None
    """
    if not len(countries) or not len(tech_map):
        return

    capacities = pm.data.IRENASTAT().powerplant.convert_country_to_alpha2()
    capacities = capacities.query(
        "Year == @year and Technology in @tech_map and Country in @countries"
    )
    capacities = capacities.groupby(["Technology", "Country"]).Capacity.sum()

    logger.info(
        f"Heuristics applied to distribute renewable capacities [GW]: "
        f"\n{capacities.groupby('Technology').sum().div(1e3).round(2)}"
    )

    for ppm_technology, techs in tech_map.items():
        tech_i = n.generators.query("carrier in @techs").index
        if ppm_technology in capacities.index.get_level_values("Technology"):
            stats = capacities.loc[ppm_technology].reindex(countries, fill_value=0.0)
        else:
            stats = pd.Series(0.0, index=countries)
        country = n.generators.bus[tech_i].map(n.buses.country)
        existent = n.generators.p_nom[tech_i].groupby(country).sum()
        missing = stats - existent
        dist = n.generators_t.p_max_pu.mean() * n.generators.p_nom_max

        n.generators.loc[tech_i, "p_nom"] += (
            dist[tech_i]
            .groupby(country)
            .transform(lambda s: normed(s) * missing[s.name])
            .where(lambda s: s > 0.1, 0.0)  # only capacities above 100kW
        )
        n.generators.loc[tech_i, "p_nom_min"] = n.generators.loc[tech_i, "p_nom"]

        if expansion_limit:
            assert np.isscalar(expansion_limit)
            logger.info(
                f"Reducing capacity expansion limit to {expansion_limit*100:.2f}% of installed capacity."
            )
            n.generators.loc[tech_i, "p_nom_max"] = (
                expansion_limit * n.generators.loc[tech_i, "p_nom_min"]
            )


def attach_line_rating(
    n, rating, s_max_pu, correction_factor, max_voltage_difference, max_line_rating
):
    # TODO: Only considers overhead lines
    n.lines_t.s_max_pu = (rating / n.lines.s_nom[rating.columns]) * correction_factor
    if max_voltage_difference:
        x_pu = (
            n.lines.type.map(n.line_types["x_per_length"])
            * n.lines.length
            / (n.lines.v_nom**2)
        )
        # need to clip here as cap values might be below 1
        # -> would mean the line cannot be operated at actual given pessimistic ampacity
        s_max_pu_cap = (
            np.deg2rad(max_voltage_difference) / (x_pu * n.lines.s_nom)
        ).clip(lower=1)
        n.lines_t.s_max_pu = n.lines_t.s_max_pu.clip(
            lower=1, upper=s_max_pu_cap, axis=1
        )
    if max_line_rating:
        n.lines_t.s_max_pu = n.lines_t.s_max_pu.clip(upper=max_line_rating)
    n.lines_t.s_max_pu *= s_max_pu


def add_transmission_projects(n, transmission_projects):
    logger.info(f"Adding transmission projects to network.")
    for path in transmission_projects:
        path = Path(path)
        df = pd.read_csv(path, index_col=0, dtype={"bus0": str, "bus1": str})
        if df.empty:
            continue
        if "new_buses" in path.name:
            n.madd("Bus", df.index, **df)
        elif "new_lines" in path.name:
            n.madd("Line", df.index, **df)
        elif "new_links" in path.name:
            n.madd("Link", df.index, **df)
        elif "adjust_lines":
            n.lines.update(df)
        elif "adjust_links":
            n.links.update(df)





######################################## PyPSA-Spain
#
# Function to attach loads according to PyPSA-Spain methodology
#

def fun_update_elec_capacities(n, carriers_to_update, method_increase, nuts2_ES_file, dic_nuts_file):


    def _update_in_network(df, required_capacity, method_increase):
        '''
        This function operates on a df = n.generators to modify 'p_nom' to match a target installed capacity
        '''

        ### Compute initial capacity
        initial_capacity = df['p_nom'].sum()

        df_updated = df.copy()

        ### Increase capacity according to defined method
        if int(required_capacity) > int(initial_capacity):

            if method_increase == 'additional':
                capacity_added_at_each_bus = (required_capacity-initial_capacity)/df.shape[0]
                df_updated.loc[:, 'p_nom'] += capacity_added_at_each_bus

                
            if (method_increase == 'proportional') and (initial_capacity>0):
                factor = required_capacity / initial_capacity
                df_updated.loc[:, 'p_nom'] *= factor


            if (method_increase == 'proportional') and (initial_capacity==0):
                print(f'########## [PyPSA-Spain] <add_electricity.py> Warning: no initial capacity for carrier {cc} in {rr_name}. Using "additional" method')
                capacity_added_at_each_bus = (required_capacity-initial_capacity)/df.shape[0]
                df_updated.loc[:, 'p_nom'] += capacity_added_at_each_bus


        ### Reduce capacity proportionally at each bus
        if int(required_capacity) < int(initial_capacity):
            factor = required_capacity / initial_capacity
            df_updated.loc[:, 'p_nom'] *= factor



        return df_updated



    ##### Define relevant variables:
    df_buses = n.buses
    df_generators = n.generators

    nuts2_ES = gpd.read_file(nuts2_ES_file)

    with open(dic_nuts_file, "r") as archivo:
        dic_nuts = yaml.safe_load(archivo)
    dic_nuts2 = dic_nuts['dic_NUTS2']


    # gdf_buses
    geometry_buses = gpd.points_from_xy(df_buses["x"], df_buses["y"])
    gdf_buses = gpd.GeoDataFrame(df_buses,geometry=geometry_buses, crs=4326)


       
    #################### Loop over carriers to update
    for cc, ff in carriers_to_update.items():


        ##### Load esios file
        df_installed_capacity = pd.read_csv(ff, index_col='datetime')



        #################### Loop over NUTS2 regions included in df_installed_capacity columns
        for rr in df_installed_capacity.columns:


            ##### Enter only if rr is in dic_NUTS2 (which contains regions only included in PyPSA-Spain)
            # This is to avoid columns such as 'total', 'Melilla', 'Canarias'... that are in rr
            if rr in dic_nuts2.values():


                # This is just for using the name in the log
                rr_name = nuts2_ES.loc[ nuts2_ES["NUTS_ID"]==rr , "NUTS_NAME"].values[0]

                    
                ########## get a list with local buses located in region rr
                gdf_region = nuts2_ES[nuts2_ES['NUTS_ID']==rr]

                gdf_buses_local = gpd.sjoin(gdf_buses, gdf_region, how="inner", predicate="within")

                list_buses_local = gdf_buses_local.index.to_list()


                ########## Get the generators in local buses and carrier cc, with capacity>0.01 (to avoid everywhere generators). 
                df_generators_local_cc = df_generators.loc[ (df_generators['bus'].isin(list_buses_local)) & (df_generators['carrier']==cc) & (df_generators['p_nom']>0.01)]
                # but if none, then include everywhere generators
                if len(df_generators_local_cc)==0:
                    df_generators_local_cc = df_generators.loc[ (df_generators['bus'].isin(list_buses_local)) & (df_generators['carrier']==cc)]
                # if still none, there is no bus with this carrier
                if len(df_generators_local_cc)==0:
                    print(f'########## [PyPSA-Spain] <add_electricity.py> Warning: No bus with generator {cc} in {rr_name}. Updating capacity was not possible.')

                else:
                    ########## Get the real capacity reported by ESIOS in that region and carrier for the desired year        
                    required_capacity = df_installed_capacity[rr].mean()

    
                    ############### Update capacities
                    df_updated = _update_in_network(df_generators_local_cc, required_capacity, method_increase)




                    ############### Some buses may have surpassed p_nom_max. Share extra capacity across the other buses

                    # Check first that there is space for all the capacity
                    if df_updated['p_nom'].sum() > df_updated['p_nom_max'].sum():
                        sys.exit(f'Not enough space to allocate required capacity of carrier {cc} in region {rr_name}')


                    df_to_sanitise = df_updated.copy()


                    while (df_updated['p_nom'] > df_updated['p_nom_max']).any():

                        # Take the first one
                        index_conflictive = df_updated[df_generators_local_cc['p_nom']>df_updated['p_nom_max']].index[0]

                        # Identify overcapacity
                        overcapacity = df_updated.at[index_conflictive,'p_nom'] - df_updated.at[index_conflictive,'p_nom_max']

                        # Remove capacity in conflictive bus at df_updated, so that it is not conflictive anymore
                        df_updated.at[index_conflictive,'p_nom'] -= overcapacity

                        # Remove conflictive bus at df_to_sanitise
                        df_to_sanitise.drop(index_conflictive, inplace=True)

                        # Share the overcapacity across the other buses
                        required_capacity = df_to_sanitise['p_nom'].sum() + overcapacity
                        df_to_sanitise = _update_in_network(df_to_sanitise, required_capacity, method_increase)


                        ############### Update df_generators_local_cc with df_to_sanitise
                        df_updated.update(df_to_sanitise)                   
                    


                    ##### Messages  

                    initial_capacity = int(df_generators_local_cc['p_nom'].sum())       
                    final_capacity = int(df_updated['p_nom'].sum())
    
                    if final_capacity == initial_capacity:
                        print(f'########## [PyPSA-Spain] <add_electricity.py> {cc} capacity matches in {rr_name}.')

                    else:
                        print(f'########## [PyPSA-Spain] <add_electricity.py> {cc} capacity in {rr_name} was updated from {initial_capacity} to {final_capacity}.')


                    saturated_nodes = (df_updated['p_nom']==df_updated['p_nom_max']).sum()
                
                    if saturated_nodes > 0:
                        print(f'Maximum capacity was reached in {saturated_nodes}!!')



                    ############### Update n.generators with df_updated
                    n.generators.update(df_updated)


#
#
#
########################################



if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake("add_electricity")
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    params = snakemake.params

    n = pypsa.Network(snakemake.input.base_network)

    if params["transmission_projects"]["enable"]:
        add_transmission_projects(n, snakemake.input.transmission_projects)

    time = get_snapshots(snakemake.params.snapshots, snakemake.params.drop_leap_day)
    n.set_snapshots(time)

    Nyears = n.snapshot_weightings.objective.sum() / 8760.0

    costs = load_costs(
        snakemake.input.tech_costs,
        params.costs,
        params.electricity["max_hours"],
        Nyears,
    )
    ppl = load_powerplants(snakemake.input.powerplants)





    ################################################## PyPSA-Spain
    #
    # Attach electricity demand according to PyPSA-Spain customisation
    #

    update_gdp_pop = snakemake.params.update_gdp_pop
    electricity_demand = snakemake.params.electricity_demand


    if electricity_demand['enable']:

        print(f'##### [PyPSA-Spain]: Using attach_load_vPyPSA_Spain to attach electricity demand in ES..')

        attach_load_vPyPSA_Spain(
            n,
            snakemake.input.regions,
            snakemake.input.load,
            snakemake.input.nuts3_shapes,
            # snakemake.input.get("gdp_pop_non_nuts3"),
            params.countries,
            params.scaling_factor,
        )

    else:
            attach_load(
            n,
            snakemake.input.regions,
            snakemake.input.load,
            snakemake.input.nuts3_shapes,
            snakemake.input.get("gdp_pop_non_nuts3"),
            params.countries,
            params.scaling_factor,
        )
    #
    #        
    ##################################################



    update_transmission_costs(n, costs, params.length_factor)

    renewable_carriers = set(params.electricity["renewable_carriers"])
    extendable_carriers = params.electricity["extendable_carriers"]
    conventional_carriers = params.electricity["conventional_carriers"]
    conventional_inputs = {
        k: v for k, v in snakemake.input.items() if k.startswith("conventional_")
    }

    if params.conventional["unit_commitment"]:
        unit_commitment = pd.read_csv(snakemake.input.unit_commitment, index_col=0)
    else:
        unit_commitment = None

    if params.conventional["dynamic_fuel_price"]:
        fuel_price = pd.read_csv(
            snakemake.input.fuel_price, index_col=0, header=0, parse_dates=True
        )
        fuel_price = fuel_price.reindex(n.snapshots).ffill()
    else:
        fuel_price = None

    attach_conventional_generators(
        n,
        costs,
        ppl,
        conventional_carriers,
        extendable_carriers,
        params.conventional,
        conventional_inputs,
        unit_commitment=unit_commitment,
        fuel_price=fuel_price,
    )

    attach_wind_and_solar(
        n,
        costs,
        snakemake.input,
        renewable_carriers,
        extendable_carriers,
        params.length_factor,
    )

    if "hydro" in renewable_carriers:
        p = params.renewable["hydro"]
        carriers = p.pop("carriers", [])
        attach_hydro(
            n,
            costs,
            ppl,
            snakemake.input.profile_hydro,
            snakemake.input.hydro_capacities,
            carriers,
            **p,
        )

    estimate_renewable_caps = params.electricity["estimate_renewable_capacities"]
    if estimate_renewable_caps["enable"]:
        if params.foresight != "overnight":
            logger.info(
                "Skipping renewable capacity estimation because they are added later "
                "in rule `add_existing_baseyear` with foresight mode 'myopic'."
            )
        else:
            tech_map = estimate_renewable_caps["technology_mapping"]
            expansion_limit = estimate_renewable_caps["expansion_limit"]
            year = estimate_renewable_caps["year"]

            if estimate_renewable_caps["from_opsd"]:
                attach_OPSD_renewables(n, tech_map)

            estimate_renewable_capacities(
                n, year, tech_map, expansion_limit, params.countries
            )

    update_p_nom_max(n)

    line_rating_config = snakemake.config["lines"]["dynamic_line_rating"]
    if line_rating_config["activate"]:
        rating = xr.open_dataarray(snakemake.input.line_rating).to_pandas().transpose()
        s_max_pu = snakemake.config["lines"]["s_max_pu"]
        correction_factor = line_rating_config["correction_factor"]
        max_voltage_difference = line_rating_config["max_voltage_difference"]
        max_line_rating = line_rating_config["max_line_rating"]

        attach_line_rating(
            n,
            rating,
            s_max_pu,
            correction_factor,
            max_voltage_difference,
            max_line_rating,
        )

    sanitize_carriers(n, snakemake.config)

    n.meta = snakemake.config




    ################################################## PyPSA-Spain
    #
    # Update elec capacities 
    #

    ##### parameters
    update_elec_capacities = snakemake.params.update_elec_capacities

    carriers_to_update = update_elec_capacities['carriers_to_update']
    method_increase = update_elec_capacities['method_increase']

    ##### inputs
    nuts2_ES_file = snakemake.input.nuts2_ES
    dic_nuts_file = snakemake.input.dic_nuts # is employed to loop over the regions considered in PyPSA-ES

    ##### call function
    if update_elec_capacities['enable']:
        fun_update_elec_capacities(n, carriers_to_update, method_increase, nuts2_ES_file, dic_nuts_file)
    #
    #
    ##########



    n.export_to_netcdf(snakemake.output[0])
