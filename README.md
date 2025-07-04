<!--
SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
SPDX-License-Identifier: CC-BY-4.0
-->




# PyPSA-Spain: An extension of PyPSA-Eur to model the Spanish Energy System

The primary motivation behind the development of PyPSA-Spain was to leverage the
benefits of a national energy model over a regional one, like the availability of specific
datasets from national organisations. Additionally, a single-country model enables higher
spatial and temporal resolution with the same computational resources due to the smaller
geographical domain. Finally, it does not require assumptions about coordinated action
between countries, making it a more suitable tool for analysing national energy policies.
To accommodate cross-border interactions, a nested model approach with PyPSA-Eur was
used, wherein electricity prices from neighbouring countries are precomputed through the
optimisation of the European energy system.

PyPSA-Spain is an up-to-date fork of PyPSA-Eur, ensuring that advancements
and bug fixes made to PyPSA-Eur are integrated. In addition, PyPSA-Spain includes a number of novel functionalities that enhance the representation
of the Spanish energy system, as compared with PyPSA-Eur. 

Find more details in [https://pypsa-spain.readthedocs.io/en/latest/](https://pypsa-spain.readthedocs.io/en/latest/)

A description of the new functionalities implemented in PyPSA-Spain is now available in this article: [https://doi.org/10.1016/j.esr.2025.101764](https://doi.org/10.1016/j.esr.2025.101764).




![PyPSA-Spain Grid Model](docs/img/base.jpg)



## Basic commands for running PyPSA-Spain

As a fork of PyPSA-Eur, PyPSA-Spain uses the same command structure.  
Note that PyPSA-Spain employs the **sector network** approach



- **Full workflow run**:

```bash
$ snakemake all --configfile config/config_ES.yaml --cores 4
```


- **Partial runs**:

```bash
##### Cluster the network
$ snakemake cluster_networks --configfile config/config_ES.yaml --cores 4
```

```bash
##### Prepare the network
$ snakemake prepare_sector_networks --configfile config/config_ES.yaml --cores 4
```

```bash
##### Solve the network
$ snakemake solve_sector_networks --configfile config/config_ES.yaml --cores 4
```

- **Run to get a specific output**, for example, `base.nc` network:

```bash
$ snakemake resources/networks/base.nc --configfile config/config_ES.yaml --cores 4
```




**Comments:**
1. Adjust the number of `--cores` according to your system.
2. Add the `-n` flag (dry-run) to check the workflow before execution 






## Licence

PyPSA-Spain is a fork of [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur), which is released as free software under the
[MIT License](https://opensource.org/licenses/MIT), see [`doc/licenses.rst`](doc/licenses.rst).
However, different licenses and terms of use may apply to the various input data.
