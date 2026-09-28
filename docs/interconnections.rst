..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
The model for Spanish interconnections
####################################################################


PyPSA-Spain includes a functionality to model power interconnections with neighbouring countries. The model described here supersedes the one presented in the seminal paper :cite:`Gallego-Castillo2025`. In that earlier version, each neighbouring country was represented by a *country bus* carrying a generator priced at the country market price and a large constant load, sized so that exports could always be absorbed. That formulation gave the right marginal incentives, but the cost of covering the foreign demand entered the objective function and was therefore mixed with the cost of covering the Spanish demand. The current model removes the country bus altogether and prices the exchange directly at each border.



Model components
========================

For each interconnection, the following elements are added to the network:

- a **border bus** with carrier ``DC_ic``, located at the user-specified coordinates.
- two unidirectional **links** between the border bus and the closest AC bus of the Spanish network: the export link flows from the Spanish grid to the border bus, and the import link flows the other way. Each has its own fixed ``p_nom``, equal to the net transfer capacity (NTC) of that direction, and carriers ``DC_ic export`` and ``DC_ic import``.
- two **market generators** attached to the border bus, one per direction, with carriers ``DC_ic market import`` and ``DC_ic market export``.


The bid-ask spread
------------------------

Importing costs :math:`\lambda_t + s/2` and exporting earns :math:`\lambda_t - s/2`, so a round trip costs exactly :math:`s` EUR/MWh. This removes the degenerate optimum in which the model imports and exports simultaneously in the same hour at no cost, and represents transaction costs. The spread is defined **per country**, in ``neighbouring_countries.yaml``.


Limits on the annual exchanged energy
--------------------------------------

Each direction of each interconnection optionally accepts an ``e_sum_max``, given in TWh/year, which is passed to the ``e_sum_max`` attribute of the corresponding market generator. This is the main lever to keep the exchange within plausible bounds. It is particularly relevant in capacity expansion runs: with exogenous prices and perfect foresight, allowing export revenues can otherwise motivate building generation or storage purely to arbitrage against the foreign price.


Configuration
========================

The functionality is enabled in the ``pypsa_spain`` module of ``config/config_ES.yaml``:

.. code-block:: yaml

   interconnections:
     enable: true
     nc_ES_file: data_ES/interconnections/neighbouring_countries.yaml
     ic_ES_file: data_ES/interconnections/interconnections.yaml

Neighbouring countries
------------------------

Each entry of ``data_ES/interconnections/neighbouring_countries.yaml`` describes one country by its price series and its spread:

.. code-block:: yaml

   FR:
     prices: data_ES/interconnections/ic_market_prices/mp_FR_2030_cutout2023.csv
     spread: 5

The price file holds the hourly market price of the country in EUR/MWhe, with a datetime index in the first column and the price in the second. The series is mapped onto the network snapshots by (month, day, hour), so a price year different from the snapshot year still works; when they differ, a warning is logged and the series is used anyway. Keeping both coherent is the user's responsibility.

The prices are averaged over the time span each snapshot represents, taken from ``n.snapshot_weightings.objective``, which keeps them consistent with the temporal averaging or the `tsam` segmentation already applied to the network.


Interconnections
------------------------

Each entry of ``data_ES/interconnections/interconnections.yaml`` describes one interconnection:

.. code-block:: yaml

   ES FR0:

     bus_name: ES FR0

     country: FR

     x: 2.877482
     y: 42.426598

     length: 34.25
     efficiency: 0.979219
     underwater_fraction: 0

     export:
       link_name: ES FR0 export
       generator_name: ES FR0 market export
       p_nom: 2800
       e_sum_max:

     import:
       link_name: ES FR0 import
       generator_name: ES FR0 market import
       p_nom: 2800
       e_sum_max:

Component names are declared explicitly rather than derived from the entry key, so that they can carry the name of the real project.

``length`` is the length of the Spanish side of the link, in km. It is taken verbatim from this file and is never recomputed from the coordinates of the buses. This is deliberate: the border bus sits at the physical crossing point, whereas the Spanish bus is the centroid of a clustered region, so the distance between the two is not the length of the real line.

The links are priced as HVDC transmission. Only the export link of each interconnection is charged: the export and import links represent the two directions of the same physical asset, so charging both would pay twice for the cable and for the converter stations. This is consistent as long as the export NTC is the larger of the two, which holds for the four interconnections shipped by default.

Four interconnections are provided by default:

- **ES FR0**: Santa Llogaia–Baixas, 2800 MW in both directions.
- **ES FR1**: Gatika–Cubnezais, 2000 MW in both directions, operational from 2028.
- **ES PT0** and **ES PT1**: a northern and a southern crossing with Portugal, 2100 MW export and 1750 MW import each, jointly reproducing the PNIEC assumption of 4200 MW export and 3500 MW import.


Modelling assumptions and limitations
========================================

- **Imported electricity is carbon-free.** The import carrier has no ``co2_emissions``, so imports do not consume the CO2 budget. It is an assumption worth making deliberately: assigning an emission factor to the carrier ``DC_ic market import`` is enough to change it.

- **Negative prices.** In high-renewable scenarios :math:`\lambda_t` can be negative, which makes importing profitable in itself. The model may then import to displace its own renewable generation. The behaviour is bounded by the NTC and by ``e_sum_max``, and the spread prevents round-trip arbitrage, but it is worth inspecting in the results.

- **Price-taker assumption.** The neighbouring country is represented by an exogenous price and an unlimited willingness to trade up to the NTC. The interconnections therefore tend to saturate whenever the price differential has the right sign, which is more extreme than the behaviour of a coupled market. A stepped price curve, in which each interconnection is split into several tranches with increasing (import) or decreasing (export) prices, would soften this; it is not implemented.

- **Negative costs in the summaries.** Export revenues appear as negative costs in ``costs.csv``, which is correct. The plotting of costs was adapted accordingly: rows are now dropped by magnitude rather than by signed value, and the lower bound of the cost axis is allowed to go below zero, otherwise negative segments were silently discarded or clipped out of view.

- **Nodal summaries.** Border buses receive a ``location`` equal to their own name in the sector-coupled stage, so nodal summaries emit rows for them with no associated geometry. This is harmless in the CSV outputs, but a map that joins by location will simply drop them.


