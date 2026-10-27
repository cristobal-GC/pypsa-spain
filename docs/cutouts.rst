..
  SPDX-FileCopyrightText: Contributors to PyPSA-Spain <https://github.com/cristobal-GC/pypsa-spain>

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Weather cutouts for the Iberian Peninsula
####################################################################


Besides the European cutouts provided by PyPSA-Eur, **PyPSA-Spain** can use two collections of atlite cutouts prepared specifically for the Iberian Peninsula and published on Zenodo. They are retrieved automatically when their names are set in the configuration file.


Available cutouts
========================

+------------------------+-----------------------------------------------+-----------------------------------------------+
|                        | ERA5-SARAH3                                   | CERRA                                         |
+========================+===============================================+===============================================+
| Name                   | ``iberia-<year>-era5-sarah3``                 | ``iberia-<year>-cerra``                       |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Years                  | 1990-2020                                     | 2019, 2020, 2023                              |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Spatial resolution     | 0.3° (~30 km)                                 | 0.05° (~5 km)                                 |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Extent                 | lon -12.0° to 6.9°, lat 33.0° to 48.0°        | lon -14.0° to 7.0°, lat 32.0° to 48.0°        |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Temporal resolution    | hourly                                        | hourly                                        |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Variables              | all atlite variables (wind, solar radiation,  | wind only (``wnd100m``, ``roughness``)        |
|                        | temperature, runoff, height)                  |                                               |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Use                    | any technology, also as ``default_cutout``    | ``onwind`` and ``offwind-*`` only             |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| File size              | ~0.85 GB per year                             | ~4.1 GB per year                              |
+------------------------+-----------------------------------------------+-----------------------------------------------+
| Source                 | `Zenodo record 22745628                       | `Zenodo record 20625166                       |
|                        | <https://zenodo.org/records/22745628>`__      | <https://zenodo.org/records/20625166>`__      |
+------------------------+-----------------------------------------------+-----------------------------------------------+

**ERA5-SARAH3 cutouts** combine ERA5 meteorological variables with direct and diffuse surface solar radiation from the satellite-based SARAH-3 dataset. SARAH-3 radiation is kept over land and over sea cells within 50 km of the coastline; farther offshore it is set to NaN.

**CERRA cutouts** are derived from the Copernicus European Regional ReAnalysis, regridded from its native Lambert conformal grid to a regular latitude-longitude grid. Hours not covered by CERRA analyses (available every 3 hours) are filled with the +1 h and +2 h forecasts of the preceding analysis.

License and attribution requirements (ERA5, CERRA and SARAH-3) are given in the Zenodo records linked above, and must be followed when using the cutouts.


Configuration
========================

Set the cutout names in the ``atlite`` and ``renewable`` sections of the configuration file. Since CERRA cutouts only contain wind variables, a cutout with all variables must be kept as ``default_cutout``. For instance, to use CERRA for wind power and ERA5-SARAH3 for everything else in 2019:

.. code:: yaml

    atlite:
      default_cutout: iberia-2019-era5-sarah3

    renewable:
      onwind:
        cutout: iberia-2019-cerra
      offwind-float:
        cutout: iberia-2019-cerra


Using cutouts already downloaded
================================

Cutouts kept in a local folder (for instance, shared by several clones of the repository) can be used instead of retrieving them again. Enable ``local_cutouts`` and set the folder that holds the ``<cutout_name>.nc`` files. Since the folder depends on the machine, set it in ``config/config.yaml``, which is not versioned and is loaded automatically when present:

.. code:: yaml

    pypsa_spain:
      local_cutouts:
        enable: true
        folder: /path/to/cutouts

Each cutout found in that folder is symlinked into the folder where the workflow expects it (e.g. ``data/cutout_iberia_cerra/primary/zenodo-20625166/``). Cutouts not found there are retrieved (or built) as usual. This applies to the upstream cutouts as well as to the Iberian ones.





Acknowledgements
========================

These cutouts and their integration in PyPSA-Spain have been developed within the project **WE-RESYST** (*Towards a Decarbonized and Resilient Iberian Power System under Meteorological Variability and Climate Change*). The authors gratefully acknowledge the support of the project. The datasets are also available in the `WE-RESYST community on Zenodo <https://zenodo.org/communities/we-resyst>`__.
