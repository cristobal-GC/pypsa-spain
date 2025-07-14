..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
The model for Spanish interconnections
####################################################################


PyPSA-Spain includes a functionality to model power interconnections with neighbouring countries.

The current interconnection model is a slight improvement on the one described in the seminal paper :cite:`Gallego-Castillo2025`.

All the required elements are added during the rule `prepare_network`. The model comprises two groups of elements, **neighbouring_countries** and **interconnections**.


Neighbouring countries
========================

For each neighbouring country, a set of elements is included:

- a **bus** (called *country bus*). 
- a **generator**, with `marginal_cost` defined in the `pypsa-spain` module of the config file.
- a **load**.

The characteristics of these elements are defined in file `data_ES/interconnections/neighbouring_countries.yaml`



Interconnections
===================

For each interconnection with a neighbouring country, a set of elements is included:

- a **bus** (called, *border bus*).
- two **links** (one export and one import) between the border bus and the closest node of the Spanish network.
- a **link** between the border bus and the country bus.

The characteristics of these elements are defined in the file `data_ES/interconnections/interconnections.yaml`





.. bibliography::




