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







