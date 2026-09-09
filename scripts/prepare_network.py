# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT


"""
Prepare PyPSA network for solving according to opts, such
as.

- adding an annual **limit** of carbon-dioxide emissions,
- adding an exogenous **price** per tonne emissions of carbon-dioxide (or other kinds),
- setting an **N-1 security margin** factor for transmission line capacities,
- specifying an expansion limit on the **cost** of transmission expansion,
- specifying an expansion limit on the **volume** of transmission expansion, and
- reducing the **temporal** resolution by averaging over multiple hours
  or segmenting time series into chunks of varying lengths using `tsam`.

Description
-----------

!!! tip
    The rule `prepare_elec_networks` runs
    for all `scenario` s in the configuration file
    the rule [prepare_network][].
"""

import logging

import numpy as np
import pandas as pd
import pypsa

import yaml   ##### Required in PyPSA-Spain
from pypsa.geo import haversine_pts   ##### Required in PyPSA-Spain

from scripts._helpers import (
    PYPSA_V1,
    configure_logging,
    get,
    load_costs,
    set_scenario_config,
    update_config_from_wildcards,
)
from scripts.add_electricity import set_transmission_costs

# Allow for PyPSA versions <0.35
if PYPSA_V1:
    from pypsa.common import expand_series
else:
    from pypsa.descriptors import expand_series


idx = pd.IndexSlice

logger = logging.getLogger(__name__)


def modify_attribute(n, adjustments, investment_year, modification="factor"):
    if not adjustments[modification]:
        return
    change_dict = adjustments[modification]
    for c in change_dict.keys():
        if c not in n.component_attrs.keys():
            logger.warning(f"{c} needs to be a PyPSA Component")
            continue
        for carrier in change_dict[c].keys():
            ind_i = (
                n.components[c].static[n.components[c].static.carrier == carrier].index
            )
            if ind_i.empty:
                continue
            for parameter in change_dict[c][carrier].keys():
                if parameter not in n.components[c].static.columns:
                    logger.warning(f"Attribute {parameter} needs to be in {c} columns.")
                    continue
                if investment_year:
                    factor = get(change_dict[c][carrier][parameter], investment_year)
                else:
                    factor = change_dict[c][carrier][parameter]
                if modification == "factor":
                    logger.info(f"Modify {parameter} of {carrier} by factor {factor} ")
                    n.components[c].static.loc[ind_i, parameter] *= factor
                elif modification == "absolute":
                    logger.info(f"Set {parameter} of {carrier} to {factor} ")
                    n.components[c].static.loc[ind_i, parameter] = factor
                else:
                    logger.warning(
                        f"{modification} needs to be either 'absolute' or 'factor'."
                    )


def maybe_adjust_costs_and_potentials(n, adjustments, investment_year=None):
    if not adjustments:
        return
    for modification in adjustments.keys():
        modify_attribute(n, adjustments, investment_year, modification)


def add_co2limit(n, co2limit, Nyears=1.0):
    n.add(
        "GlobalConstraint",
        "CO2Limit",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=co2limit * Nyears,
    )


def add_gaslimit(n, gaslimit, Nyears=1.0):
    sel = n.carriers.index.intersection(["OCGT", "CCGT", "CHP"])
    n.carriers.loc[sel, "gas_usage"] = 1.0

    n.add(
        "GlobalConstraint",
        "GasLimit",
        carrier_attribute="gas_usage",
        sense="<=",
        constant=gaslimit * Nyears,
    )


def add_emission_prices(n, emission_prices={"co2": 0.0}, exclude_co2=False):
    if exclude_co2:
        emission_prices.pop("co2")
    ep = (
        pd.Series(emission_prices).rename(lambda x: x + "_emissions")
        * n.carriers.filter(like="_emissions")
    ).sum(axis=1)
    gen_ep = n.generators.carrier.map(ep) / n.generators.efficiency
    n.generators["marginal_cost"] += gen_ep
    n.generators_t["marginal_cost"] += gen_ep[n.generators_t["marginal_cost"].columns]
    su_ep = n.storage_units.carrier.map(ep) / n.storage_units.efficiency_dispatch
    n.storage_units["marginal_cost"] += su_ep


def add_dynamic_emission_prices(n, fn):
    co2_price = (
        pd.read_csv(fn, index_col=0, parse_dates=True).squeeze().reindex(n.snapshots)
    )

    emissions = (
        n.generators.carrier.map(n.carriers.co2_emissions) / n.generators.efficiency
    )
    co2_cost = expand_series(emissions, n.snapshots).T.mul(co2_price, axis=0)

    static = n.generators.marginal_cost
    dynamic = n.get_switchable_as_dense("Generator", "marginal_cost")

    marginal_cost = dynamic + co2_cost.reindex(columns=dynamic.columns, fill_value=0)
    n.generators_t.marginal_cost = marginal_cost.loc[:, marginal_cost.ne(static).any()]

    # remove the static marginal cost from generators with dynamic marginal cost
    affected = co2_cost.where(co2_cost > 0).dropna(axis=1).columns
    n.generators.loc[affected, "marginal_cost"] = 0.0


def set_line_s_max_pu(n, s_max_pu=0.7):
    n.lines["s_max_pu"] = s_max_pu
    logger.info(f"N-1 security margin of lines set to {s_max_pu}")


def set_transmission_limit(n, kind, factor, costs, Nyears=1):
    links_dc_b = n.links.carrier == "DC" if not n.links.empty else pd.Series()

    _lines_s_nom = (
        np.sqrt(3)
        * n.lines.type.map(n.line_types.i_nom)
        * n.lines.num_parallel
        * n.lines.bus0.map(n.buses.v_nom)
    )
    lines_s_nom = n.lines.s_nom.where(n.lines.type == "", _lines_s_nom)

    col = "capital_cost" if kind == "c" else "length"
    ref = (
        lines_s_nom @ n.lines[col]
        + n.links.loc[links_dc_b, "p_nom"] @ n.links.loc[links_dc_b, col]
    )

    set_transmission_costs(n, costs)

    if factor == "opt" or float(factor) > 1.0:
        n.lines["s_nom_min"] = lines_s_nom
        n.lines["s_nom_extendable"] = True

        n.links.loc[links_dc_b, "p_nom_min"] = n.links.loc[links_dc_b, "p_nom"]
        n.links.loc[links_dc_b, "p_nom_extendable"] = True

    if factor != "opt":
        con_type = "expansion_cost" if kind == "c" else "volume_expansion"
        rhs = float(factor) * ref
        n.add(
            "GlobalConstraint",
            f"l{kind}_limit",
            type=f"transmission_{con_type}_limit",
            sense="<=",
            constant=rhs,
            carrier_attribute="AC, DC",
        )

    return n


def average_every_nhours(n, offset, drop_leap_day=False):
    logger.info(f"Resampling the network to {offset}")
    m = n.copy(snapshots=[])

    snapshot_weightings = n.snapshot_weightings.resample(offset).sum()
    sns = snapshot_weightings.index
    if drop_leap_day:
        sns = sns[~((sns.month == 2) & (sns.day == 29))]
    m.set_snapshots(snapshot_weightings.index)
    m.snapshot_weightings = snapshot_weightings

    for c in n.components:
        pnl = getattr(m, c.list_name + "_t")
        for k, df in c.dynamic.items():
            if not df.empty:
                pnl[k] = df.resample(offset).mean()

    return m


def apply_time_segmentation(n, segments, solver_name="cbc"):
    logger.info(f"Aggregating time series to {segments} segments.")
    try:
        import tsam
    except ImportError:
        raise ModuleNotFoundError(
            "Optional dependency 'tsam' not found.Install via 'pip install tsam'"
        )

    p_max_pu_norm = n.generators_t.p_max_pu.max()
    p_max_pu = n.generators_t.p_max_pu / p_max_pu_norm

    load_norm = n.loads_t.p_set.max()
    load = n.loads_t.p_set / load_norm

    inflow_norm = n.storage_units_t.inflow.max()
    inflow = n.storage_units_t.inflow / inflow_norm

    raw = pd.concat([p_max_pu, load, inflow], axis=1, sort=False)

    if hasattr(tsam, "aggregate"):  # tsam >= 3.0
        agg = tsam.aggregate(
            raw,
            n_clusters=1,
            period_duration=len(raw),
            segments=tsam.SegmentConfig(n_segments=int(segments)),
        )
        agg = agg.cluster_representatives
    else:  # tsam < 3.0
        from tsam import timeseriesaggregation

        agg = timeseriesaggregation.TimeSeriesAggregation(
            raw,
            hoursPerPeriod=len(raw),
            noTypicalPeriods=1,
            noSegments=int(segments),
            segmentation=True,
            solver=solver_name,
        )

        segmented = agg.createTypicalPeriods()

    weightings = segmented.index.get_level_values("Segment Duration")
    offsets = np.insert(np.cumsum(weightings[:-1]), 0, 0)
    snapshots = [n.snapshots[0] + pd.Timedelta(f"{offset}h") for offset in offsets]

    n.set_snapshots(pd.DatetimeIndex(snapshots, name="name"))
    n.snapshot_weightings = pd.Series(
        weightings, index=snapshots, name="weightings", dtype="float64"
    )

    segmented.index = snapshots
    n.generators_t.p_max_pu = segmented[n.generators_t.p_max_pu.columns] * p_max_pu_norm
    n.loads_t.p_set = segmented[n.loads_t.p_set.columns] * load_norm
    n.storage_units_t.inflow = segmented[n.storage_units_t.inflow.columns] * inflow_norm

    return n


def enforce_autarky(n, only_crossborder=False):
    if only_crossborder:
        lines_rm = n.lines.loc[
            n.lines.bus0.map(n.buses.country) != n.lines.bus1.map(n.buses.country)
        ].index
        links_rm = n.links.loc[
            n.links.bus0.map(n.buses.country) != n.links.bus1.map(n.buses.country)
        ].index
    else:
        lines_rm = n.lines.index
        links_rm = n.links.loc[n.links.carrier == "DC"].index
    n.remove("Line", lines_rm)
    n.remove("Link", links_rm)


def set_line_nom_max(
    n,
    s_nom_max_set=np.inf,
    p_nom_max_set=np.inf,
    s_nom_max_ext=np.inf,
    p_nom_max_ext=np.inf,
):
    if np.isfinite(s_nom_max_ext) and s_nom_max_ext > 0:
        logger.info(f"Limiting line extensions to {s_nom_max_ext} MW")
        n.lines["s_nom_max"] = n.lines["s_nom"] + s_nom_max_ext

    if np.isfinite(p_nom_max_ext) and p_nom_max_ext > 0:
        logger.info(f"Limiting link extensions to {p_nom_max_ext} MW")
        hvdc = n.links.index[n.links.carrier == "DC"]
        n.links.loc[hvdc, "p_nom_max"] = n.links.loc[hvdc, "p_nom"] + p_nom_max_ext

    n.lines["s_nom_max"] = n.lines.s_nom_max.clip(upper=s_nom_max_set)
    n.links["p_nom_max"] = n.links.p_nom_max.clip(upper=p_nom_max_set)





######################################## PyPSA-Spain
#
# Functions to add interconnections
#
# Each interconnection is represented by a border bus (carrier 'DC_ic') linked to
# the closest AC bus of the Spanish network by two unidirectional links, plus two
# market generators attached to the border bus:
#
#   '<ic> market import'   sign=+1, marginal_cost = +(price(t) + spread/2)
#   '<ic> market export'   sign=-1, marginal_cost = -(price(t) - spread/2)
#
# The export generator has sign=-1, so it withdraws power from the border bus
# (PyPSA builds the nodal balance as sign * Generator-p), while the objective
# function uses marginal_cost * p without the sign. A negative marginal cost
# therefore turns exports into revenue, and the objective contains only the
# Spanish system cost plus purchases minus export revenues. There is no
# neighbouring-country bus and no foreign demand to cover.
#

IC_CARRIER_BUS = 'DC_ic'
IC_CARRIER_LINK = {'export': 'DC_ic export', 'import': 'DC_ic import'}
IC_CARRIER_MARKET = {'export': 'DC_ic market export', 'import': 'DC_ic market import'}

### Sign of the market generator dispatch at the border bus, per direction:
### imports inject into the Spanish side, exports withdraw from it.
IC_MARKET_SIGN = {'export': -1.0, 'import': 1.0}


def _read_ic_price_series(n, country, nc_params):
    """
    Read the hourly market price series of a neighbouring country and align it to
    n.snapshots.

    The CSV carries its own datetime index, whose year is the year the series
    corresponds to. It is mapped onto the snapshots by (month, day, hour), so a
    price year different from the snapshot year still works: it only triggers a
    warning, since keeping both coherent is the user's responsibility.

    Values are averaged over the time span each snapshot represents, taken from
    n.snapshot_weightings.objective. This keeps the series consistent with the
    temporal averaging or tsam segmentation already applied to the network.
    """
    fn = nc_params['prices']
    prices = pd.read_csv(fn, index_col=0, parse_dates=True).squeeze('columns')

    if isinstance(prices, pd.DataFrame):
        raise ValueError(
            f"[PyPSA-Spain] Price file {fn} for {country} must have a datetime "
            f"index and a single value column, found columns {list(prices.columns)}."
        )
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError(
            f"[PyPSA-Spain] Price file {fn} for {country} has no datetime index. "
            f"Its first column must hold the timestamp of each hourly price, so "
            f"that the year of the series is known."
        )

    ##### Warn if the price year and the snapshot years disagree
    price_years = sorted({ts.year for ts in prices.index})
    snapshot_years = sorted({ts.year for ts in n.snapshots})
    if not set(price_years) & set(snapshot_years):
        logger.warning(
            f"########## [PyPSA-Spain] <prepare_network.py> WARNING: price series "
            f"for {country} ({fn}) covers {price_years}, but the snapshots cover "
            f"{snapshot_years}. The series is mapped by (month, day, hour) anyway; "
            f"make sure the price file is coherent with the modelled year."
        )

    ##### Average the hourly prices over the span each snapshot represents.
    ###
    ### This is needed for the electricity-only stage: if 'clustering: temporal:
    ### resolution_elec' is set, average_every_nhours or apply_time_segmentation
    ### run earlier in this same script, before the interconnections exist, so the
    ### snapshots reaching this point are already aggregated and sampling them
    ### would take the price of the first hour of each span as representative.
    ###
    ### It is not needed for the sector stage: 'resolution_sector' is applied later,
    ### in prepare_sector_network, where average_every_nhours resamples the
    ### marginal_cost of this generator with .mean() like any other time series.
    ###
    ### Every hour covered by the snapshots is expanded at once and grouped back,
    ### so this stays vectorised even for a full hourly year.
    hourly = pd.Series(
        prices.to_numpy(),
        index=pd.MultiIndex.from_arrays(
            [prices.index.month, prices.index.day, prices.index.hour]
        ),
    )

    durations = n.snapshot_weightings.objective.reindex(n.snapshots).fillna(1.0)
    spans = durations.round().astype(int).clip(lower=1).to_numpy()

    ### One timestamp per hour covered, plus the index of the snapshot it belongs to
    starts = np.repeat(n.snapshots.to_numpy(), spans)
    within = np.arange(spans.sum()) - np.repeat(spans.cumsum() - spans, spans)
    stamps = pd.DatetimeIndex(starts) + pd.to_timedelta(within, unit='h')
    owner = np.repeat(np.arange(len(n.snapshots)), spans)

    values = hourly.reindex(
        pd.MultiIndex.from_arrays([stamps.month, stamps.day, stamps.hour])
    ).to_numpy()

    ### Mean of the hours that do have a price, NaN for snapshots with none
    found = ~np.isnan(values)
    totals = np.bincount(
        owner, weights=np.where(found, values, 0.0), minlength=len(n.snapshots)
    )
    counts = np.bincount(owner, weights=found, minlength=len(n.snapshots))
    aligned = pd.Series(
        np.where(counts > 0, totals / np.maximum(counts, 1), np.nan),
        index=n.snapshots,
    )

    if aligned.isna().all():
        raise ValueError(
            f"[PyPSA-Spain] Could not align any price of {fn} ({country}) to the "
            f"network snapshots."
        )
    if aligned.isna().any():
        missing = aligned.index[aligned.isna()]
        logger.warning(
            f"########## [PyPSA-Spain] <prepare_network.py> WARNING: {len(missing)} "
            f"snapshots have no price in {fn} ({country}), e.g. {missing[0]} "
            f"(a leap day is the usual reason). They are filled by interpolation."
        )
        aligned = aligned.interpolate().ffill().bfill()

    return aligned


def _closest_spanish_ac_bus(n, x0, y0, ic_name):
    """
    Return the AC bus of the Spanish network closest to (x0, y0).

    Candidates are selected by attributes (carrier and country), not by matching
    substrings on bus names, which depend on the clustering mode and silently
    change the result.
    """
    candidates = n.buses.loc[
        (n.buses['carrier'] == 'AC') & (n.buses['country'] == 'ES'), ['x', 'y']
    ]

    if candidates.empty:
        raise ValueError(
            f"[PyPSA-Spain] No AC bus with country 'ES' found in the network while "
            f"adding interconnection {ic_name}. Cannot attach it to the Spanish grid."
        )

    distances = pd.Series(
        haversine_pts(
            np.array([[x0, y0]]), candidates[['x', 'y']].to_numpy()
        ),
        index=candidates.index,
    )
    closest = distances.idxmin()

    logger.info(
        f"########## [PyPSA-Spain] <prepare_network.py> INFO: interconnection "
        f"{ic_name} attached to bus {closest} ({distances[closest]:.1f} km away)"
    )

    return closest


def attach_interconnections_ES(n, ic_dic, nc_dic):
    """
    Add the electrical interconnections with the neighbouring countries.

    Parameters
    ----------
    ic_dic : dict
        Contents of interconnections.yaml, one entry per interconnection.
    nc_dic : dict
        Contents of neighbouring_countries.yaml, one entry per country, holding
        its price series and its bid-ask spread.
    """
    ##### Register carriers (idempotent, so re-running on a prepared network is safe)
    ac_color = n.carriers.at['AC', 'color'] if 'AC' in n.carriers.index else ''
    for carrier in [IC_CARRIER_BUS, *IC_CARRIER_LINK.values(), *IC_CARRIER_MARKET.values()]:
        if carrier not in n.carriers.index:
            n.add('Carrier', name=carrier, color=ac_color, nice_name=carrier)

    ##### Price series per country, aligned to the snapshots (read once per country)
    prices = {
        country: _read_ic_price_series(n, country, params)
        for country, params in nc_dic.items()
    }

    ##### Length of the optimization horizon, to scale annual energy limits
    n_years = n.snapshot_weightings.generators.sum() / 8760.0

    for ic_name, ic in ic_dic.items():

        logger.info(
            f'########## [PyPSA-Spain] <prepare_network.py> INFO: Adding '
            f'interconnection {ic_name}'
        )

        country = ic['country']
        if country not in nc_dic:
            raise ValueError(
                f"[PyPSA-Spain] Interconnection {ic_name} refers to country "
                f"'{country}', which is not defined in neighbouring_countries.yaml."
            )

        ##### Bid-ask spread of the country, in EUR/MWh
        ic_spread = float(nc_dic[country].get('spread') or 0.0)

        ##### Closest AC bus of the Spanish network, resolved before adding any
        ##### component of this interconnection
        closest_bus = _closest_spanish_ac_bus(n, ic['x'], ic['y'], ic_name)

        ##### Border bus
        bus_name = ic['bus_name']
        n.add(
            'Bus',
            bus_name,
            x=ic['x'],
            y=ic['y'],
            carrier=IC_CARRIER_BUS,
            country='ES',
        )

        efficiency = ic.get('efficiency', 1.0)
        length = ic.get('length', 0.0)
        ### Share of the length running under water, used by set_transmission_costs
        ### to split the cable cost between 'HVDC overhead' and 'HVDC submarine'
        underwater_fraction = float(ic.get('underwater_fraction') or 0.0)

        for direction in ['export', 'import']:

            p_nom = ic[direction]['p_nom']

            ##### Link between the Spanish network and the border bus.
            ### Exports flow ES -> border, imports flow border -> ES.
            link_name = ic[direction]['link_name']
            bus0, bus1 = (
                (closest_bus, bus_name) if direction == 'export'
                else (bus_name, closest_bus)
            )
            n.add(
                'Link',
                link_name,
                bus0=bus0,
                bus1=bus1,
                carrier=IC_CARRIER_LINK[direction],
                p_nom=p_nom,
                p_nom_extendable=False,
                length=length,
                efficiency=efficiency,
                lifetime=50,
            )
            ### p_nom_min pins the capacity, otherwise prepare_sector_network.py
            ### ends up setting p_nom=0
            n.links.loc[link_name, 'p_nom_min'] = p_nom
            n.links.loc[link_name, 'underwater_fraction'] = underwater_fraction

            ##### Market generator at the border bus
            generator_name = ic[direction]['generator_name']
            n.add(
                'Generator',
                generator_name,
                bus=bus_name,
                carrier=IC_CARRIER_MARKET[direction],
                sign=IC_MARKET_SIGN[direction],
                p_nom=p_nom,
                p_nom_extendable=False,
                efficiency=1,
                capital_cost=0,
            )

            ### Importing pays price + spread/2, exporting earns price - spread/2.
            ### The export generator has sign=-1 but its dispatch variable p stays
            ### positive, so the revenue comes from the negative marginal cost.
            marginal_cost = prices[country] + 0.5 * ic_spread
            if direction == 'export':
                marginal_cost = -(prices[country] - 0.5 * ic_spread)
            n.generators_t['marginal_cost'][generator_name] = marginal_cost

            ##### Optional cap on the annual energy exchanged, given in TWh/year
            e_sum_max = ic[direction].get('e_sum_max')
            if e_sum_max is not None:
                n.generators.loc[generator_name, 'e_sum_max'] = (
                    float(e_sum_max) * 1e6 * n_years
                )
#
#
########################################




# %%
if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake(
            "prepare_network",
            clusters="50",
            opts="",
        )
    configure_logging(snakemake)  # pylint: disable=E0606
    set_scenario_config(snakemake)
    update_config_from_wildcards(snakemake.config, snakemake.wildcards)

    n = pypsa.Network(snakemake.input[0])
    Nyears = n.snapshot_weightings.objective.sum() / 8760.0
    costs = load_costs(snakemake.input.costs)

    set_line_s_max_pu(n, snakemake.params.lines["s_max_pu"])

    # temporal averaging
    time_resolution = snakemake.params.time_resolution
    is_string = isinstance(time_resolution, str)
    if is_string and time_resolution.lower().endswith("h"):
        n = average_every_nhours(n, time_resolution, snakemake.params.drop_leap_day)

    # segments with package tsam
    if is_string and time_resolution.lower().endswith("seg"):
        solver_name = snakemake.config["solving"]["solver"]["name"]
        segments = int(time_resolution.replace("seg", ""))
        n = apply_time_segmentation(n, segments, solver_name)

    if snakemake.params.co2limit_enable:
        add_co2limit(n, snakemake.params.co2limit, Nyears)

    if snakemake.params.gaslimit_enable:
        add_gaslimit(n, snakemake.params.gaslimit, Nyears)

    maybe_adjust_costs_and_potentials(n, snakemake.params["adjustments"])

    emission_prices = snakemake.params.emission_prices
    if emission_prices["dynamic"]:
        logger.info(
            "Setting time dependent emission prices according spot market price"
        )
        add_dynamic_emission_prices(n, snakemake.input.co2_price)
    elif emission_prices["enable"]:
        if isinstance(emission_prices["co2"], dict):
            logger.warning(
                "Not setting emission prices on generators and storage units, "
                "due to their configuration per planning horizon"
            )
        elif isinstance(emission_prices["co2"], float):
            add_emission_prices(n, dict(co2=emission_prices["co2"]))

    kind = snakemake.params.transmission_limit[0]
    factor = snakemake.params.transmission_limit[1:]
    set_transmission_limit(n, kind, factor, costs, Nyears)

    set_line_nom_max(
        n,
        s_nom_max_set=snakemake.params.lines.get("s_nom_max", np.inf),
        p_nom_max_set=snakemake.params.links.get("p_nom_max", np.inf),
        s_nom_max_ext=snakemake.params.lines.get("max_extension", np.inf),
        p_nom_max_ext=snakemake.params.links.get("max_extension", np.inf),
    )



    ################################################## PyPSA-Spain
    #
    # This is a nice moment to add interconnections with PT and FR, after functions:
    #
    #   - set_transmission_limit, which enforces "extendable=True" for lines and links if 'transmission_limit' > 1 or 'opt'
    #
    #   - set_line_nom_max, which enforces s_nom_max and p_nom_max to inf unless global limits specified in config.yaml for lines and links
    #
    
    interconnections = snakemake.params.interconnections

    if interconnections['enable']:

        with open(interconnections['nc_ES_file'], 'r') as f:
            nc_dic = yaml.safe_load(f)

        with open(interconnections['ic_ES_file'], 'r') as f:
            ic_dic = yaml.safe_load(f)

        attach_interconnections_ES(n, ic_dic, nc_dic)

        ##### set_transmission_costs already ran inside set_transmission_limit,
        ### before these links existed, so it is called again to price them. It
        ### assigns capital_cost rather than accumulating it, so lines and regular
        ### DC links are left unchanged.
        set_transmission_costs(n, costs)
    #
    #
    ########################################





    if snakemake.params.autarky["enable"]:
        only_crossborder = snakemake.params.autarky["by_country"]
        enforce_autarky(n, only_crossborder=only_crossborder)

    n.meta = dict(snakemake.config, **dict(wildcards=dict(snakemake.wildcards)))
    n.export_to_netcdf(snakemake.output[0])
