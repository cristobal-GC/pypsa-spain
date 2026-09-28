..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Electricity demand
####################################################################

By default, PyPSA-Eur builds the electricity demand of each country from ENTSO-E and OPSD
series and distributes it spatially using the same time profile. PyPSA-Spain
replaces this for Spain with a construction based on national statistics disaggregated by
economic sector and NUTS region.
The annual demand of the modelled year is set explicitly in the configuration, and is then
distributed in time and space using normalised profiles and shares per economic sector and
NUTS region. In particular, for the spatial disaggregation onto the model regions, the
demand is distributed over the intersection of every (bus region, NUTS region) pair.


Configuration
========================

The functionality is enabled in the ``pypsa_spain`` module of ``config/config_ES.yaml``:

.. code-block:: yaml

   electricity_demand:
     enable: true
     annual_value: 270
     profiles: data_ES/electricity_demand/electricity_demand_profiles_NUTS3_by_economicSector_2022.csv
     percentages: data_ES/electricity_demand/electricity_demand_percentages_NUTS3_by_economicSector_2022.csv

``enable``
  Activates the Spanish demand construction. When disabled, the PyPSA-Eur methodology is used.

``annual_value``
  Total annual electricity demand of the modelled year, in TWh.

``profiles``
  CSV with the normalised hourly profiles per NUTS region and economic sector.

``percentages``
  CSV with the share of the annual demand corresponding to each NUTS region and economic sector.
