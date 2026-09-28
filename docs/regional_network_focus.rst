..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Spain Authors

  SPDX-License-Identifier: CC-BY-4.0


####################################################################
Regional network focus
####################################################################

This functionality allows clustering the base network with additional weighting applied to a selected region (NUTS-2 or NUTS-3).
The resulting clustered network achieves higher spatial resolution within that area, enabling more detailed analyses, such as grid congestion or renewable resource assessment, while keeping the overall network size limited.


The following figures show the obtained clusterd networks with 50 clusters focused on País Vasco (left) and Aragón (right) regions. 
The parameter *k_focus* defines the intensity of the weighting applied to the focus region.


+---------------------------------------------+---------------------------------------------+
| .. image:: img/network_ES21_k_100_50.png    | .. image:: img/network_ES24_k_100_50.png    |
|    :width: 100%                             |    :width: 100%                             |
+---------------------------------------------+---------------------------------------------+


Configuration
========================

The functionality is enabled in the ``pypsa_spain`` module of ``config/config_ES.yaml``:

.. code-block:: yaml

   regional_network_focus:
     enable: false
     region_NUTS: 'ES511'
     k_focus: 50

``enable``
  Activates the focused clustering.

``region_NUTS``
  Code of the region to focus on. A four-character code is interpreted as NUTS-2 and a
  five-character one as NUTS-3.

``k_focus``
  Intensity of the weighting applied to the focus region. A value of 1 reproduces the
  unweighted PyPSA-Eur clustering.
