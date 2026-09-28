<!--
SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
SPDX-FileCopyrightText: Contributors to PyPSA-Spain <https://github.com/cristobal-GC/pypsa-spain>
SPDX-License-Identifier: CC-BY-4.0
-->

[![GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/cristobal-GC/pypsa-spain?include_prereleases)](https://github.com/cristobal-GC/pypsa-spain/releases)
[![Documentation](https://readthedocs.org/projects/pypsa-spain/badge/?version=latest)](https://pypsa-spain.readthedocs.io/en/latest/?badge=latest)
![Size](https://img.shields.io/github/repo-size/cristobal-GC/pypsa-spain)
[![Paper](https://img.shields.io/badge/DOI-10.1016%2Fj.esr.2025.101764-blue)](https://doi.org/10.1016/j.esr.2025.101764)
[![Snakemake](https://img.shields.io/badge/snakemake-≥9-brightgreen.svg?style=flat)](https://snakemake.readthedocs.io)
[![Discord](https://img.shields.io/discord/911692131440148490?logo=discord)](https://discord.gg/AnuJBk23FU)



# PyPSA-Spain: An extension of PyPSA-Eur to model the Spanish Energy System

The primary motivation behind the development of PyPSA-Spain was to leverage the
benefits of a national energy model over a regional one, like the availability of specific
datasets from national organisations. Additionally, a single-country model enables higher
spatial and temporal resolution with the same computational resources due to the smaller
geographical domain. Finally, it does not require assumptions about coordinated action
between countries, making it a more suitable tool for analysing national energy policies.
To accommodate cross-border interactions, a nested model approach with PyPSA-Eur can be
used, wherein electricity prices from neighbouring countries are precomputed through the
optimisation of the European energy system.

PyPSA-Spain is an up-to-date fork of PyPSA-Eur, ensuring that advancements
and bug fixes made to PyPSA-Eur are integrated. In addition, PyPSA-Spain includes a number of novel functionalities that enhance the representation
of the Spanish energy system, as compared with PyPSA-Eur. 

Find more details in [https://pypsa-spain.readthedocs.io/en/latest/](https://pypsa-spain.readthedocs.io/en/latest/)

A description of the new functionalities implemented in PyPSA-Spain is available in: [https://doi.org/10.1016/j.esr.2025.101764](https://doi.org/10.1016/j.esr.2025.101764).




![PyPSA-Spain Grid Model](docs/img/base.jpg)



## Basic commands for running PyPSA-Spain

As a fork of PyPSA-Eur, PyPSA-Spain uses the same command structure. The
workflow runs through five stages:

```
base -> simplified -> clustered -> composed_{horizon} -> solved_{horizon}
```

Only two wildcards are utilized: `{horizon}`, the planning year, and `{run}`, which appears when
`run.scenarios.enable` is true.


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
##### Compose the network
$ snakemake compose_networks --configfile config/config_ES.yaml --cores 4
```

```bash
##### Solve the network
$ snakemake solve_networks --configfile config/config_ES.yaml --cores 4
```

- **Run to get a specific output**, for example, `base.nc` network:

```bash
$ snakemake resources/networks/base.nc --configfile config/config_ES.yaml --cores 4
```




**Comments:**
1. Adjust the number of `--cores` according to your computer system.
2. Add the `-n` flag (dry-run) to check the workflow before execution.
3. `config/config_ES.yaml` is loaded by the `Snakefile` as the default configuration, so
   passing it with `--configfile` is optional; it is shown here for clarity. A local
   `config/config.yaml`, if present, overrides it.






## Credits

PyPSA-Spain is a fork of [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur), the open
optimisation model of the European energy system, and tracks it closely so that upstream
advances and bug fixes are incorporated. The upstream model and its sector-coupled
extension are archived at:

[![Zenodo PyPSA-Eur](https://zenodo.org/badge/DOI/10.5281/zenodo.3520874.svg)](https://doi.org/10.5281/zenodo.3520874)
[![Zenodo PyPSA-Eur-Sec](https://zenodo.org/badge/DOI/10.5281/zenodo.3938042.svg)](https://doi.org/10.5281/zenodo.3938042)

If you use PyPSA-Spain in your research, please cite:

> Gallego-Castillo, C. and Victoria, M. (2025). *PyPSA-Spain: An extension of PyPSA-Eur to
> model the Spanish energy system.* Energy Strategy Reviews 60, 101764.
> [10.1016/j.esr.2025.101764](https://doi.org/10.1016/j.esr.2025.101764)

See [`CITATION.cff`](CITATION.cff) for the machine-readable version.


## Licence

PyPSA-Spain is a fork of [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur), which is released as free software under the
[MIT License](https://opensource.org/licenses/MIT), see [`doc/licenses.md`](doc/licenses.md).
However, different licenses and terms of use may apply to the various input data.
