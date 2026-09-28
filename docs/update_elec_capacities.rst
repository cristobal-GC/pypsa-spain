..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Update of installed electricity capacities
####################################################################

The initial installed capacities that PyPSA-Eur assigns to each model region come from
``powerplantmatching``, which for distributed technologies such as wind and solar does not
always reproduce the regional totals reported by the Spanish system operator.

PyPSA-Spain can correct this by **rescaling the installed capacity of selected carriers so
that it matches the values reported by ESIOS per NUTS-2 region**. The reported regional
capacity is apportioned among the model regions in proportion to the area each of them
shares with the NUTS-2 region, and the existing distribution between buses within a region
is preserved. Where a region has no initial capacity for that carrier, the capacity is added
instead of scaled. Both ``p_nom`` and ``p_nom_min`` are updated, and every adjustment is
written to the log.


Configuration
========================

The functionality is enabled in the ``pypsa_spain`` module of ``config/config_ES.yaml``:

.. code-block:: yaml

   update_elec_capacities:
     enable: false
     carriers_to_update:
       onwind: data_ES/esios/esios_onwind_capacity_2023.csv
       solar: data_ES/esios/esios_solar_capacity_2023.csv

``enable``
  Activates the capacity update.

``carriers_to_update``
  One entry per carrier to correct, pointing to the CSV with the capacity reported per
  NUTS-2 region. Carriers not listed keep the capacities assigned by PyPSA-Eur.

.. note::

   ``solar`` and ``solar-hsat`` describe the same fleet from the point of view of the
   reported statistics, so only one of the two should be listed.
