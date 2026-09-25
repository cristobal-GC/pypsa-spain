# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT


"""
Prepare PyPSA network for solving with various operational constraints and
temporal adjustments.

- adding an annual **limit** of carbon-dioxide emissions,
- adding an exogenous **price** per tonne emissions of carbon-dioxide (or other kinds),
- setting an **N-1 security margin** factor for transmission line capacities,
- specifying an expansion limit on the **cost** of transmission expansion,
- specifying an expansion limit on the **volume** of transmission expansion, and
- reducing the **temporal** resolution by averaging over multiple hours
  or segmenting time series into chunks of varying lengths using `tsam`.

"""

import logging
import os
from collections.abc import Mapping

import numpy as np
import pandas as pd
import pypsa

import yaml   ##### Required in PyPSA-Spain
from pypsa.geo import haversine_pts   ##### Required in PyPSA-Spain

from scripts._helpers import (
    PYPSA_V1,
    get,
)
from scripts.add_electricity import set_transmission_costs
from scripts.co2_budget import (
    bound_value_for_horizon,
    co2_budget_for_horizon,
    co2_limit_name,
)
from scripts.prepare_sector_network import co2_emissions_year, set_temporal_aggregation

# Allow for PyPSA versions <0.35
if PYPSA_V1:
    from pypsa.common import expand_series
else:
    from pypsa.descriptors import expand_series


idx = pd.IndexSlice

logger = logging.getLogger(__name__)

# Conversion constant for CO2 emissions
GT_TO_TONNES = 1e9  # Gigatonnes to tonnes conversion


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


def add_co2limit(
    n: pypsa.Network,
    co2_max: float | None = None,
    co2_min: float | None = None,
    nyears: float = 1.0,
    suffix: str = "",
    investment_period: int | None = None,
    glc_type: str = "primary_energy",
) -> None:
    """
    Add global CO2 emissions constraint(s) to the network.

    Parameters
    ----------
    n : pypsa.Network
        Network to add constraints to
    co2_max : float or None, default None
        Annual CO2 emissions limit in Gt CO2/a.
    co2_min : float or None, default None
        Annual minimum CO2 emissions limit in Gt CO2/a.
    nyears : float, default 1.0
        Number of years represented by the modelled snapshots. The global constraint constant
        is computed as the annual value times ``nyears`` (converted to tonnes).
    suffix : str, default ""
        Suffix to add to constraint names
    investment_period : int | None, default None
        Investment period for perfect foresight constraints. When set, the global
        constraint applies only to that investment period.
    glc_type : str, default "primary_energy"
        GlobalConstraint type determining which solver routine enforces it:
        ``primary_energy`` (PyPSA native, electricity-only), ``co2_atmosphere``
        (sector atmosphere-store constraint) or ``Co2Budget`` (cumulative perfect
        foresight budget).

    """

    for value, sense, bound in [(co2_max, "<=", "upper"), (co2_min, ">=", "lower")]:
        if value is None:
            continue

        if pd.isna(value):
            raise ValueError(f"CO2 {bound} limit value cannot be NaN.")

        n.add(
            "GlobalConstraint",
            co2_limit_name(bound) + suffix,
            type=glc_type,
            investment_period=investment_period,
            carrier_attribute="co2_emissions",
            sense=sense,
            constant=value * GT_TO_TONNES * nyears,
        )


def _is_scalar_bound(bound: object) -> bool:
    return isinstance(bound, (int, float))


def _is_mapping_bound(bound: object) -> bool:
    return isinstance(bound, Mapping)


def apply_co2_budget_constraints(
    n: pypsa.Network,
    *,
    inputs,
    params,
    nyears: float,
    current_horizon: int,
) -> None:
    foresight = params.foresight
    horizons = params.horizons

    co2_budget = params["co2_budget"]
    upper_cfg = co2_budget["upper"]
    lower_cfg = co2_budget["lower"]

    if upper_cfg is None and lower_cfg is None:
        logger.info(
            f"No CO2 budget specified for horizon {current_horizon}. "
            "Skipping CO2 constraints for this horizon."
        )
        return

    for bound, cfg in [("upper", upper_cfg), ("lower", lower_cfg)]:
        if cfg is not None and not (_is_scalar_bound(cfg) or _is_mapping_bound(cfg)):
            raise TypeError(
                f"co2_budget.{bound} must be null, a number, or a dict mapping year "
                f"to value. Received {type(cfg).__name__}."
            )

    if (
        upper_cfg is not None
        and lower_cfg is not None
        and _is_scalar_bound(upper_cfg) != _is_scalar_bound(lower_cfg)
    ):
        raise ValueError(
            "Invalid co2_budget configuration: co2_budget.upper and co2_budget.lower "
            "must both be scalars or both be dicts mapping year to value."
        )

    budget_is_scalar = _is_scalar_bound(
        upper_cfg if upper_cfg is not None else lower_cfg
    )

    baseline_1990 = None
    if co2_budget["relative"]:
        upper_raw = bound_value_for_horizon(upper_cfg, current_horizon)
        lower_raw = bound_value_for_horizon(lower_cfg, current_horizon)
        if upper_raw is not None or lower_raw is not None:
            baseline_1990 = co2_emissions_year(
                countries=params.countries,
                input_eurostat=inputs["eurostat"],
                options=params.sector,
                emissions_scope=co2_budget["emissions_scope"],
                input_co2=inputs["co2"],
                year=1990,
            )

    upper, lower = co2_budget_for_horizon(
        co2_budget,
        current_horizon=current_horizon,
        baseline_1990=baseline_1990,
    )

    is_last_horizon = current_horizon == horizons[-1]
    elec_only = not params.sector["enabled"]
    glc_type = "primary_energy" if elec_only else "co2_atmosphere"

    if budget_is_scalar:
        if foresight == "perfect" and not is_last_horizon:
            logger.info(
                f"Deferring scalar CO2 constraint until final horizon {horizons[-1]}."
            )
            return
        if foresight == "perfect":
            # cumulative budget over all periods: recompute nyears from the
            # merged multi-period network rather than the per-horizon value
            nyears = n.snapshot_weightings.objective.sum() / 8760.0
            if elec_only:
                add_co2limit(n, upper, lower, nyears, glc_type="primary_energy")
            else:
                add_co2limit(
                    n,
                    upper,
                    lower,
                    nyears,
                    glc_type="Co2Budget",
                    investment_period=horizons[-1],
                )
            return
        add_co2limit(n, upper, lower, nyears, glc_type=glc_type)
        return

    if foresight == "perfect":
        add_co2limit(
            n,
            upper,
            lower,
            nyears,
            suffix=f"-{current_horizon}",
            investment_period=current_horizon,
            glc_type=glc_type,
        )
    else:
        add_co2limit(n, upper, lower, nyears, glc_type=glc_type)


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


def cap_transmission_capacity(
    n,
    line_max=None,
    link_max=None,
    line_max_extension=None,
    link_max_extension=None,
    line_max_pu=None,
    link_max_pu=None,
):
    """
    Cap transmission capacity for AC lines and DC links.

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network instance
    line_max : float, optional
        Absolute upper limit for AC line capacity [MW]. If None, no limit is applied.
    link_max : float, optional
        Absolute upper limit for DC link capacity [MW]. If None, no limit is applied.
    line_max_extension : float, optional
        Maximum extension per AC line [MW]. If None, no limit is applied.
    link_max_extension : float, optional
        Maximum extension per DC link [MW]. If None, no limit is applied.
    line_max_pu : float, optional
        Set N-1 security margin for AC lines (e.g., 0.7 for 70% utilization).
        If None, s_max_pu is not modified.
    link_max_pu : float, optional
        Set maximum utilization for DC links (e.g., 0.7 for 70% utilization).
        If None, p_max_pu is not modified.

    Notes
    -----
    All parameters accept None to skip that particular constraint. This allows
    selective application of limits without needing to specify all parameters.
    """
    # Set N-1 security margin (s_max_pu) for AC lines if specified
    if line_max_pu is not None:
        n.lines["s_max_pu"] = line_max_pu
        logger.info(f"N-1 security margin of lines set to {line_max_pu}")

    # Set maximum utilization (p_max_pu) for DC links if specified
    if link_max_pu is not None:
        hvdc = n.links.index[n.links.carrier == "DC"]
        n.links.loc[hvdc, "p_max_pu"] = link_max_pu
        logger.info(f"Maximum utilization of DC links set to {link_max_pu}")

    # Apply line capacity extension limit if specified
    if (
        line_max_extension is not None
        and np.isfinite(line_max_extension)
        and line_max_extension > 0
    ):
        logger.info(f"Limiting AC line extensions to {line_max_extension} MW")
        n.lines["s_nom_max"] = n.lines["s_nom"] + line_max_extension

    # Apply link capacity extension limit if specified
    if (
        link_max_extension is not None
        and np.isfinite(link_max_extension)
        and link_max_extension > 0
    ):
        logger.info(f"Limiting DC link extensions to {link_max_extension} MW")
        hvdc = n.links.index[n.links.carrier == "DC"]
        n.links.loc[hvdc, "p_nom_max"] = n.links.loc[hvdc, "p_nom"] + link_max_extension

    # Apply absolute line capacity limit if specified
    if line_max is not None and np.isfinite(line_max):
        n.lines["s_nom_max"] = n.lines.s_nom_max.clip(upper=line_max)

    # Apply absolute link capacity limit if specified
    if link_max is not None and np.isfinite(link_max):
        n.links["p_nom_max"] = n.links.p_nom_max.clip(upper=link_max)





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


def _price_interconnection_links(n, costs, link_length_factor=1.0):
    """
    Price the interconnection links like regular HVDC links.   ##### PyPSA-Spain

    Only the export link of each interconnection is priced: export and import model the
    two directions of the same physical asset, so charging both would pay twice for the
    cable and the converter stations.

    This replaces the second, global call to set_transmission_costs that PyPSA-Spain used
    to make. Since #1838 prepare_network.main() runs AFTER the sector layer, and a global
    call would overwrite n.lines["capital_cost"] and every carrier == "DC" link cost that
    the sector layer had already set.
    """
    ic = n.links.index[n.links.carrier == IC_CARRIER_LINK['export']]
    if ic.empty:
        return

    n.links.loc[ic, "capital_cost"] = (
        n.links.loc[ic, "length"]
        * link_length_factor
        * (
            (1.0 - n.links.loc[ic, "underwater_fraction"])
            * costs.at["HVDC overhead", "capital_cost"]
            + n.links.loc[ic, "underwater_fraction"]
            * costs.at["HVDC submarine", "capital_cost"]
        )
        + costs.at["HVDC inverter pair", "capital_cost"]
    )
#
#
########################################


def apply_temporal_aggregation(
    n: pypsa.Network,
    inputs,
    params,
) -> None:
    logger.info("Applying temporal aggregation")
    n_new = set_temporal_aggregation(
        n, params.clustering_temporal, inputs.snapshot_weightings
    )
    n.__dict__.update(n_new.__dict__)
    logger.info("Completed temporal aggregation")


def main(
    n: pypsa.Network,
    inputs,
    params,
    costs: pd.DataFrame,
    nyears: float,
) -> None:
    logger.info("Preparing network for solving")

    electricity_cfg = params.electricity

    if electricity_cfg["gaslimit_enable"]:
        gaslimit = electricity_cfg["gaslimit"]
        add_gaslimit(n, gaslimit, nyears)

    emission_prices = params.costs["emission_prices"]

    if emission_prices["dynamic"]:
        if not os.path.exists(inputs.co2_price):
            raise ValueError("CO2 price file for monthly prices not found")
        add_dynamic_emission_prices(n, inputs.co2_price)
    elif emission_prices["enable"]:
        if isinstance(emission_prices["co2"], dict):
            logger.warning(
                "Not setting emission prices specified per planning horizon. "
                "Use dynamic emission prices instead."
            )
        else:
            add_emission_prices(n, {"co2": emission_prices["co2"]}, exclude_co2=False)

    transmission_limit = electricity_cfg["transmission_limit"]
    if isinstance(transmission_limit, str):
        kind = transmission_limit[0]
        factor = transmission_limit[1:] or "opt"
    elif isinstance(transmission_limit, (list, tuple)) and len(transmission_limit) == 2:
        kind, factor = transmission_limit
    else:
        raise ValueError(
            "transmission_limit must be a string like 'c1.25' or a (kind, factor) pair"
        )

    set_transmission_limit(n, kind, factor, costs, nyears)

    cap_transmission_capacity(
        n,
        line_max=params.lines["s_nom_max"],
        link_max=params.links["p_nom_max"],
        line_max_extension=params.lines["s_nom_max_extension"],
        link_max_extension=params.links["p_nom_max_extension"],
        line_max_pu=params.lines["s_max_pu"],
        link_max_pu=params.links["p_max_pu"],
    )

    #################### PyPSA-Spain
    #
    # Add the interconnections with PT and FR. This is still the right moment, after:
    #   - set_transmission_limit, which forces extendable=True for lines and links when
    #     'transmission_limit' is > 1 or 'opt'
    #   - cap_transmission_capacity (upstream's replacement for set_line_nom_max), which
    #     caps s_nom_max / p_nom_max
    #
    # Since #1838 this runs inside main(), which compose_network.py calls AFTER the sector
    # layer and AFTER apply_temporal_aggregation. The relative order "aggregate first,
    # attach interconnections second" is unchanged, so _read_ic_price_series still averages
    # prices over snapshots that are already aggregated.
    #
    interconnections = params.interconnections

    if interconnections['enable']:

        with open(interconnections['nc_ES_file'], 'r') as f:
            nc_dic = yaml.safe_load(f)

        with open(interconnections['ic_ES_file'], 'r') as f:
            ic_dic = yaml.safe_load(f)

        attach_interconnections_ES(n, ic_dic, nc_dic)

        ##### These links did not exist when set_transmission_costs ran inside
        ### add_electricity.main(), so they are priced here. link_length_factor is left at
        ### 1.0 to reproduce the pre-#1838 numbers; see "Anomalias detectadas / A-1" in
        ### the migration plan before changing it to params.links["length_factor"].
        _price_interconnection_links(n, costs, link_length_factor=1.0)
    #
    ####################
