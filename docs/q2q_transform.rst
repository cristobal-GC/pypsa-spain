..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Quantile-to-quantile transform of renewable profiles
####################################################################

The capacity factor time series that PyPSA-Eur derives from reanalysis data are known to
deviate systematically from observed generation. This is especially the case for wind power, which is clearly understimated by ERA5 in Spain.
PyPSA-Spain can correct this by applying a
quantile-to-quantile (q2q) transform to the modelled profiles, so that their
distribution matches the one observed historically for the Spanish system.

The transform is a per-technology mapping, fitted beforehand and stored as a pickled
interpolation function. Details on how these functions are obtained are available in the
`Q2Q repository <https://github.com/cristobal-GC/Q2Q_repository>`__.

The correction is applied to the capacity factor profiles of each technology for which a
transform file is provided. Technologies left empty keep the uncorrected profiles.


Configuration
========================

The functionality is enabled in the ``pypsa_spain`` module of ``config/config_ES.yaml``:

.. code-block:: yaml

   q2q_transform:
     enable: true
     onwind: data_ES/q2q/q2q_onwind_REF_v2.pkl
     offwind-ac: ''
     offwind-dc: ''
     offwind-float: ''
     solar: ''
     solar-hsat: ''

``enable``
  Activates the transform.

``<carrier>``
  Path to the pickled transform for that carrier. One entry per renewable carrier; an
  empty string means no correction is applied to it.
