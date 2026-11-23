# SPDX-FileCopyrightText: Contributors to PyPSA-Spain <https://github.com/cristobal-GC/pypsa-spain>
#
# SPDX-License-Identifier: MIT

#################### PyPSA-Spain
#
# The annual H2 amounts of H2_valley_demands and H2_imports_exports are turned into a
# constant power. This checks, with the H2 files shipped in data_ES/H2, that the energy
# delivered follows the modelled period (full year, one month, one week) and is not
# altered by temporal averaging (168h).
#
####################

from pathlib import Path

import pandas as pd
import pypsa
import pytest
import yaml

from scripts.prepare_sector_network import (
    HOURS_PER_YEAR,
    MWH_PER_MT_H2,
    annual_H2_to_constant_power,
    attach_H2_imports_exports,
    attach_H2_valley_demands,
)

ROOT = Path(__file__).resolve().parents[1]
VALLEYS_FILE = ROOT / "data_ES/H2/H2_valley_demands.yaml"
IMPORTS_EXPORTS_FILE = ROOT / "data_ES/H2/H2_imports_exports.yaml"


def _weekly_year():
    """Full 2019 averaged to 168h: 52 snapshots of 168 h and a last one of 24 h."""
    snapshots = pd.date_range("2019-01-01", periods=53, freq="168h")
    weights = [168.0] * 52 + [HOURS_PER_YEAR - 52 * 168.0]
    return snapshots, weights


CASES = {
    "year_1h": (pd.date_range("2019-01-01", "2019-12-31 23:00", freq="h"), None),
    "month_1h": (pd.date_range("2019-03-01", "2019-03-31 23:00", freq="h"), None),
    "week_1h": (pd.date_range("2019-03-01", "2019-03-07 23:00", freq="h"), None),
    "year_168h": _weekly_year(),
}


def _network(snapshots, weights):
    n = pypsa.Network()
    n.set_snapshots(snapshots)
    if weights is not None:
        n.snapshot_weightings.loc[:, :] = pd.Series(weights, index=snapshots).values[:, None]
    # Two H2 buses in peninsular Spain (Madrid and Barcelona areas)
    n.add("Bus", ["ES0 0 H2", "ES0 1 H2"], carrier="H2", x=[-3.70, 2.15], y=[40.42, 41.39])
    return n


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("case", CASES)
def test_H2_valley_demands_energy_follows_modelled_period(case):
    snapshots, weights = CASES[case]
    n = _network(snapshots, weights)
    valleys = _load(VALLEYS_FILE)
    annual_total = sum(v["valley_params"]["demand"] for v in valleys.values())

    attach_H2_valley_demands(n, valleys)

    modelled_hours = n.snapshot_weightings.generators.sum()
    energy = n.loads.p_set.sum() * modelled_hours
    expected = annual_total * MWH_PER_MT_H2 * modelled_hours / HOURS_PER_YEAR
    assert energy == pytest.approx(expected, rel=1e-9)
    # The power does not depend on the modelled period nor on the averaging
    assert n.loads.p_set.sum() == pytest.approx(annual_H2_to_constant_power(annual_total))


@pytest.mark.parametrize("case", CASES)
def test_H2_imports_exports_energy_follows_modelled_period(case):
    snapshots, weights = CASES[case]
    n = _network(snapshots, weights)
    points = _load(IMPORTS_EXPORTS_FILE)

    attach_H2_imports_exports(n, points)

    modelled_hours = n.snapshot_weightings.generators.sum()
    share = modelled_hours / HOURS_PER_YEAR
    for name, point in points.items():
        p_set = n.loads.at[point["load_name"], "p_set"]
        sign = -1 if point["type"] == "import" else 1
        expected = sign * point["annual_amount"] * MWH_PER_MT_H2 * share
        assert p_set * modelled_hours == pytest.approx(expected, rel=1e-9), name
        # The border link is sized to the same constant power
        assert n.links.at[point["link_name"], "p_nom"] == pytest.approx(abs(p_set)), name


def test_full_year_delivers_the_annual_amount():
    """Over a full year, hourly or averaged, the whole annual amount is delivered."""
    for case in ("year_1h", "year_168h"):
        snapshots, weights = CASES[case]
        assert _network(snapshots, weights).snapshot_weightings.generators.sum() == HOURS_PER_YEAR
