..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Eur Authors

  SPDX-License-Identifier: CC-BY-4.0

.. _config:

##########################################
pypsa_spain module
##########################################

Features included in PyPSA-Spain are configured in a new module within the ``config/config_ES.yaml`` file. 



.. _pypsa_spain:

``pypsa_spain``
================


.. literalinclude:: ../config/config_ES.yaml
   :language: yaml
   :start-at: pypsa_spain:
   :end-before: # docs in https:

Each option is described in the page of the corresponding feature, listed under
**Features** in the sidebar.



.. _config_files:

Configuration files
====================

PyPSA-Spain keeps its own defaults in separate files, so that the files inherited from
PyPSA-Eur stay identical to upstream and never conflict when the fork is updated.
The ``Snakefile`` loads them in this order, each one overriding the previous:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - File
     - Role
   * - ``config/config.default.yaml``
     - PyPSA-Eur defaults. Identical to upstream, never edited here.
   * - ``config/plotting.default.yaml``
     - PyPSA-Eur plotting defaults. Identical to upstream, never edited here.
   * - ``config/config_ES.yaml``
     - PyPSA-Spain defaults: the ``pypsa_spain`` module and the Spanish values.
   * - ``config/plotting_ES.yaml``
     - PyPSA-Spain plotting deviations: Iberian map extent, reduced carrier lists,
       and the labels and colours of the ``DC_ic*`` and ``H2_ic*`` carriers.
   * - ``config/config.yaml``
     - Optional local overrides. Not tracked by git.
   * - ``--configfile``
     - Passed on the command line. Wins over all of the above.

Every value in ``config_ES.yaml`` and ``plotting_ES.yaml`` that deviates from the
PyPSA-Eur default is marked with ``#####`` at the end of the line, so the deviations can
be listed with a single ``grep`` when updating the fork.

.. note::

   Deviations belong in the ``_ES`` files, not in the upstream ones. Upstream rewrites
   ``plotting.default.yaml`` on every release cycle and validates that it matches its own
   schema, so editing it directly produces a conflict on every update and breaks
   ``test_plotting_default_yaml_in_sync``.
