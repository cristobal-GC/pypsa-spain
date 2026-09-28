..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Alternative industry scenarios
####################################################################

The future industrial production that drives the industrial energy demand of the
sector-coupled model is derived by PyPSA-Eur from historical production and a set of
built-in assumptions.

PyPSA-Spain allows **replacing that projection with a user-provided one**, so that
alternative industrial scenarios for Spain can be explored without modifying the workflow.
When the functionality is enabled, the supplied CSV is used instead of the default
projection of future industrial production per country; when it is disabled, the PyPSA-Eur
default applies.

The file must follow the same format as the one it replaces, that is, the same index of
industrial subsectors and the same units.


Configuration
========================

The functionality is enabled in the ``pypsa_spain`` module of ``config/config_ES.yaml``:

.. code-block:: yaml

   industry_scenario:
     enable: false
     industry_scenario_file: data_ES/industry/industrial_production_per_country_alternative.csv

``enable``
  Activates the alternative scenario.

``industry_scenario_file``
  Path to the CSV with the industrial production per country to be used instead of the default.
