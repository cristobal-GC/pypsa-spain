..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Electricity demand
####################################################################

By default, PyPSA-Eur builds the electricity demand of each country from ENTSO-E and OPSD
series and distributes it spatially with a combination of GDP and population. PyPSA-Spain
replaces this for Spain with a construction based on **national statistics disaggregated by
economic sector and NUTS region**.

The annual demand of the modelled year is set explicitly in the configuration, and is then
distributed in time and space using normalised profiles and shares per economic sector and
NUTS region. This makes the projected demand an explicit modelling assumption instead of an
extrapolation, which matters when the scenario includes large new consumers such as
electrolysers.

.. note::

   If the hydrogen consumed in H2 valleys is modelled separately (see
   :doc:`H2_valley_demands`), the electricity used to produce it must not be included in
   ``annual_value``, or it would be counted twice.

In addition, the spatial disaggregation onto the model regions is refined: instead of
assigning each region to a single NUTS area, the demand is distributed over the
intersection of every (bus region, NUTS region) pair.


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
