..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


PyPSA-Spain: an extension of PyPSA-Eur to model the Spanish energy system
=======================================================================================

PyPSA-Spain is an open-source model of the Spanish energy system based on the European model PyPSA-Eur.
The primary motivation behind the development of PyPSA-Spain was to leverage the benefits of a national energy model over a regional one, like the availability of specific datasets from national organisations. Additionally, a single-country model enables higher spatial and temporal resolution with the same computational resources due to the smaller geographical domain. Finally, it does not require assumptions about coordinated action between countries, making it a more suitable tool for analysing national energy policies. To accommodate cross-border interactions, a nested model approach with PyPSA-Eur was used, wherein electricity prices from neighbouring countries are precomputed through the optimisation of the European energy system.

PyPSA-Spain is an up-to-date fork of PyPSA-Eur, ensuring that advancements and bug fixes made to PyPSA-Eur are integrated. In addition, PyPSA-Spain includes a number of novel functionalities that enhance the representation of the Spanish energy system, as compared with PyPSA-Eur. 

Find the details of PyPSA-Spain v.0.0.0 in the `seminal paper <https://doi.org/10.1016/j.esr.2025.101764>`__. For updates, see the `Release Notes <https://pypsa-spain.readthedocs.io/en/latest/release_notes.html>`__.




.. image:: img/base.jpg
    :width: 70%
    :align: center



.. toctree::
   :hidden:
   :maxdepth: 1

   :caption: Configuration
   configuration

   :caption: Features
   ISA_index
   
   :caption: References
   publications
   release_notes
   
