
..
  SPDX-FileCopyrightText: Contributors to PyPSA-Spain <https://github.com/cristobal-GC/pypsa-spain>

  SPDX-License-Identifier: CC-BY-4.0

##########################################
Release Notes
##########################################


Upcoming Release
================

* New functionality ``H2_imports_exports``: Allows the imposition of H2 imports and exports at an arbitrary number of border points. The default values represent the CelZa and BarMar H2 interconnections, which belong to the H2med project and have been approved as PCIs.



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
