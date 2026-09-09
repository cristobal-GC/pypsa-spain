..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
The model for Spanish interconnections
####################################################################


PyPSA-Spain includes a functionality to model power interconnections with neighbouring countries.

The model described here supersedes the one presented in the seminal paper :cite:`Gallego-Castillo2025`. In that earlier version, each neighbouring country was represented by a *country bus* carrying a generator priced at the country market price and a large constant load, sized so that exports could always be absorbed. That formulation gave the right marginal incentives, but the cost of covering the foreign demand entered the objective function and was therefore mixed with the cost of covering the Spanish demand (see :ref:`equivalence` below). The current model removes the country bus altogether and prices the exchange directly at each border.

All the required elements are added during the rule `prepare_network`, after ``set_transmission_limit`` and ``set_line_nom_max`` so that the interconnections are not affected by the global transmission limits, and before ``enforce_autarky``.


Model components
========================

For each interconnection, the following elements are added to the network:

- a **border bus** with carrier ``DC_ic``, located at the user-specified coordinates and assigned ``country = 'ES'``.
- two unidirectional **links** between the border bus and the closest AC bus of the Spanish network: the export link flows from the Spanish grid to the border bus, and the import link flows the other way. Each has its own fixed ``p_nom``, equal to the net transfer capacity (NTC) of that direction, and carriers ``DC_ic export`` and ``DC_ic import``.
- two **market generators** attached to the border bus, one per direction, with carriers ``DC_ic market import`` and ``DC_ic market export``.

The closest Spanish bus is identified at runtime as the one minimising the great-circle distance (``pypsa.geo.haversine_pts``) to the border coordinates, among the buses with ``carrier == 'AC'`` and ``country == 'ES'``.

The border buses are virtual: they are created after ``cluster_network`` has produced the Voronoi cells, so they have no associated region. Consequently they are also absent from ``pop_layout`` and are therefore excluded from all the spatial machinery of the sector-coupled stage (heat, industry, transport). They are purely electrical nodes.

The bid-ask spread
------------------------

Importing costs :math:`\lambda_t + s/2` and exporting earns :math:`\lambda_t - s/2`, so a round trip costs exactly :math:`s` EUR/MWh. This removes the degenerate optimum in which the model imports and exports simultaneously in the same hour at no cost, and represents transaction costs. The spread is defined **per country**, in ``neighbouring_countries.yaml``, and setting it to zero reproduces the pricing of the previous country-bus model.


Limits on the annual exchanged energy
--------------------------------------

Each direction of each interconnection optionally accepts an ``e_sum_max``, given in TWh/year, which is passed to the ``e_sum_max`` attribute of the corresponding market generator. PyPSA applies it as

.. math::

   \sum_t p_t \, w_t \leq e^{\max}

where :math:`w_t` are the snapshot weightings, so the limit is correctly enforced at any temporal resolution. The value given in the YAML is scaled internally by the length of the optimization horizon, :math:`\sum_t w_t / 8760`.

This is the main lever to keep the exchange within plausible bounds. It is particularly relevant in capacity expansion runs: with exogenous prices and perfect foresight, allowing export revenues can otherwise motivate building generation or storage purely to arbitrage against the foreign price.


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
     spread: 0

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

The links are priced as HVDC transmission. ``set_transmission_costs`` has been extended to cover the carrier ``DC_ic export``, and it is called a second time in `prepare_network` after the interconnections have been added, because the first call happens inside ``set_transmission_limit``, before they exist. The capital cost per MW is

.. math::

   c = \ell \left[ (1 - f)\, c_{\text{overhead}} + f\, c_{\text{submarine}} \right] + c_{\text{inverter}}

where :math:`\ell` is ``length`` and :math:`f` is ``underwater_fraction``. The cost is charged on the **export link only**: the two links model the two directions of the same physical asset, so charging both would pay twice for the cable and for the converter stations. This is consistent as long as the export NTC is the larger of the two, which holds for the four interconnections shipped by default.

Because the links are not extendable, this capital cost is a constant of the objective function: it changes the reported system cost but not the dispatch or the investment decisions. It shows up in ``costs.csv`` under the carrier ``DC_ic export``.

Two caveats on the cost data. First, ``costs.csv`` also has an ``HVDC underground`` row, considerably more expensive than overhead, but ``set_transmission_costs`` only interpolates between overhead and submarine, so underground stretches are inevitably priced as overhead. Second, the fixed ``HVDC inverter pair`` term dominates on short crossings: for ``ES FR0`` it amounts to 57,606 EUR/MW/a against 1,850 EUR/MW/a of cable, so the result is far more sensitive to the capacity than to ``length``.

``length`` therefore drives the cable part of the cost, while the effect of the distance on losses enters through ``efficiency``, which applies to both links and follows

.. math::

   \eta = 0.98 \cdot 0.977^{\,\ell / 1000}

with :math:`\ell` in km, which reproduces the values that the ``sector: transmission_efficiency`` block of PyPSA-Eur used to apply to the interconnections in the sector-coupled stage. Set it to 1 to model lossless interconnections. Note that, unlike in the previous model, these losses now also apply in the electricity-only stage.

Four interconnections are provided by default:

- **ES FR0**: Santa Llogaia–Baixas, 2800 MW in both directions.
- **ES FR1**: Gatika–Cubnezais, 2000 MW in both directions, operational from 2028.
- **ES PT0** and **ES PT1**: a northern and a southern crossing with Portugal, 2100 MW export and 1750 MW import each, jointly reproducing the PNIEC assumption of 4200 MW export and 3500 MW import.


Modelling assumptions and limitations
========================================

- **Imported electricity is carbon-free.** The import carrier has no ``co2_emissions``, so imports do not consume the CO2 budget. This matches the behaviour of the previous model, but it is an assumption worth making deliberately: assigning an emission factor to the carrier ``DC_ic market import`` is enough to change it.

- **Negative prices.** In high-renewable scenarios :math:`\lambda_t` can be negative, which makes importing profitable in itself. The model may then import to displace its own renewable generation. The behaviour is bounded by the NTC and by ``e_sum_max``, and the spread prevents round-trip arbitrage, but it is worth inspecting in the results.

- **Price-taker assumption.** The neighbouring country is represented by an exogenous price and an unlimited willingness to trade up to the NTC. The interconnections therefore tend to saturate whenever the price differential has the right sign, which is more extreme than the behaviour of a coupled market. A stepped price curve, in which each interconnection is split into several tranches with increasing (import) or decreasing (export) prices, would soften this; it is not implemented.

- **Negative costs in the summaries.** Export revenues appear as negative costs in ``costs.csv``, which is correct. The plotting of costs was adapted accordingly: rows are now dropped by magnitude rather than by signed value, and the lower bound of the cost axis is allowed to go below zero, otherwise negative segments were silently discarded or clipped out of view.

- **Nodal summaries.** Border buses receive a ``location`` equal to their own name in the sector-coupled stage, so nodal summaries emit rows for them with no associated geometry. This is harmless in the CSV outputs, but a map that joins by location will simply drop them.


.. bibliography::
  :filter: docname in docnames
