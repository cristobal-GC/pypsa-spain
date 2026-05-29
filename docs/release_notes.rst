
..
  SPDX-FileCopyrightText: Contributors to PyPSA-Spain <https://github.com/cristobal-GC/pypsa-spain>

  SPDX-License-Identifier: CC-BY-4.0

##########################################
Release Notes
##########################################


Upcoming Release
================




PyPSA-Spain v2026.05.0 (29th May 2026)
===========================================


* New functionality ``H2_imports_exports``: Allows the imposition of H2 imports and exports at an arbitrary number of border points. The default values represent the CelZa and BarMar H2 interconnections, which belong to the H2med project and have been approved as PCIs.

* H2 valley demand templates: the repository now ships ready-to-use YAML templates for hydrogen valley demands — one built from the IDAE resolution awarding the first call of the Spanish renewable hydrogen incentive programme (`PR #18 <https://github.com/cristobal-GC/pypsa-spain/pull/18>`__), and one built from the Enagás *Call for Interest* process, with demand projections for 2030 and 2040 (`PR #21 <https://github.com/cristobal-GC/pypsa-spain/pull/21>`__). See details `here <https://pypsa-spain.readthedocs.io/en/latest/H2_valley_demands.html>`__.

* The nuclear phase-out scenario has been hardcoded in the build_powerplants rule, as the DateOut field for nuclear power plants is missing (`PR #17 <https://github.com/cristobal-GC/pypsa-spain/pull/17>`__).

* New functionality ``industry_scenario``: Allows feeding the ``build_industrial_production_per_country_tomorrow`` rule with a user-provided CSV file instead of the default ``industrial_production_per_country.csv``, enabling alternative industrial scenarios for Spain (`PR #19 <https://github.com/cristobal-GC/pypsa-spain/pull/19>`__).

* New functionality ``pop_layouts_HR``: High-resolution population layouts built from ~8,100 Spanish municipalities, replacing the default PyPSA-Eur procedure that assumes uniform population within each NUTS3 region, see details `here <https://pypsa-spain.readthedocs.io/en/latest/pop_layouts.html>`__. (`PR #20 <https://github.com/cristobal-GC/pypsa-spain/pull/20>`__)
  


PyPSA-Spain v2025.11.0 (25th November 2025)
===========================================


* New functionality ``regional_network_focus``: Allows increasing the clustering resolution within a selected region (NUTS-2 or NUTS-3), so that a larger share of the total clusters is allocated there (see details `here <https://pypsa-spain.readthedocs.io/en/latest/regional_network_focus.html>`__).

* A flexible and simple model for green hydrogen valleys through configurable, discrete hydrogen demands.

* The interconnection model with PT and FR has been upgraded (see details `here <https://pypsa-spain.readthedocs.io/en/latest/interconnections.html>`__).

* Land elegibility based on the Spanish Environmental Sensitivity Index (ISA) (see details `here <https://pypsa-spain.readthedocs.io/en/latest/ISA_index.html>`__).

* Functionality ``update_elec_capacities`` has been improved to work fine with a reduced number of clusters.
  


PyPSA-Spain v2025.04.0 (23th April 2025)
========================================

* New release based on PyPSA-Eur v2025.04.0.

* Includes new Q2Q transforms for improved renewable profile estimation. These transforms have been obtained from the recently developed `Q2Q_repository <https://github.com/cristobal-GC/Q2Q_repository>`__.





PyPSA-Spain v0.0.0 (12th December 2024)
========================================

This is the first release of PyPSA-Spain. The details are described in the seminal paper :cite:`Gallego-Castillo2025`.


.. bibliography::
  :filter: docname in docnames
