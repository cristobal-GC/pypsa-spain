#################### PyPSA-Spain
#
# Retrieve rules for the datasets added by PyPSA-Spain. Their versions and URLs are listed
# in data_ES/versions_ES.csv, which config_ES.yaml adds to data:version_files.
#
# Kept apart from rules/retrieve.smk, and included at the end of the Snakefile, so that
# upstream files stay untouched. The storage provider declared in rules/retrieve.smk is
# reused here.
#

import os
import re
from pathlib import Path
from shutil import copy2
from zipfile import ZipFile



#################### Retrieve Spanish "Indice de Sensibilidad Ambiental" (ISA)
#
#
#

if (ISA_ONWIND_DATASET := dataset_version("isa_onwind"))["source"] in [
    "primary",
    "archive",
]:

    rule retrieve_isa_onwind:
        """Retrieves the Spanish environmental sensitivity index (ISA) for onshore wind from MITECO."""
        input:
            zip_file=storage(ISA_ONWIND_DATASET["url"]),
        output:
            tiff_file="data_ES/ISA/Clas_ISA_eol_pb.tiff", # f"{ISA_ONWIND_DATASET['folder']}/Clas_ISA_eol_pb.tiff",
        run:
            from pathlib import Path
            import os

            # Check output folder does exist
            Path(output["tiff_file"]).parent.mkdir(parents=True, exist_ok=True)

            # Open zip and get TIFF file
            with ZipFile(input["zip_file"], "r") as zf:
                tiff_name_in_zip = "Clas_ISA_eol_pb.tiff"
                with zf.open(tiff_name_in_zip) as src, open(output["tiff_file"], "wb") as dst:
                    dst.write(src.read())


if (ISA_SOLAR_DATASET := dataset_version("isa_solar"))["source"] in [
    "primary",
    "archive",
]:

    rule retrieve_isa_solar:
        """Retrieves the Spanish environmental sensitivity index (ISA) for solar PV from MITECO."""
        input:
            zip_file=storage(ISA_SOLAR_DATASET["url"]),
        output:
            tiff_file="data_ES/ISA/Clas_ISA_ftv_pb.tiff", # f"{ISA_SOLAR_DATASET['folder']}/Clas_ISA_ftv_pb.tiff",
        run:
            from pathlib import Path
            import os

            # Check output folder does exist
            Path(output["tiff_file"]).parent.mkdir(parents=True, exist_ok=True)

            # Open zip and get TIFF file
            with ZipFile(input["zip_file"], "r") as zf:
                tiff_name_in_zip = "Clas_ISA_ftv_pb.tiff"
                with zf.open(tiff_name_in_zip) as src, open(output["tiff_file"], "wb") as dst:
                    dst.write(src.read())

#
#
#
####################



#################### Retrieve Spanish municipality-level population data from Zenodo
#
#
#

if (POP_ES_MUNICIPALITIES_DATASET := dataset_version("pop_ES_municipalities"))["source"] in [
    "primary",
    "archive",
]:

    rule retrieve_pop_ES_municipalities:
        """Retrieves Spanish municipality-level population data from Zenodo."""
        input:
            geojson=storage(POP_ES_MUNICIPALITIES_DATASET["url"]),
        output:
            geojson="data_ES/pop/2023/pop_ES_2023_LR.geojson",
        run:
            from pathlib import Path

            # Ensure output folder exists
            Path(output["geojson"]).parent.mkdir(parents=True, exist_ok=True)
            copy2(input["geojson"], output["geojson"])

#
#
#
####################



#################### Retrieve weather cutouts for the Iberian Peninsula from Zenodo
#
#   - cutout_iberia_era5_sarah3: iberia-<year>-era5-sarah3, https://zenodo.org/records/22745628
#   - cutout_iberia_cerra:       iberia-<year>-cerra,       https://zenodo.org/records/20625166
#
# They can be used by name anywhere a cutout is set in the config (atlite:default_cutout,
# renewable:<carrier>:cutout, ...), also mixed with the upstream cutouts of the 'cutout'
# dataset. CERRA cutouts only contain wind variables and are meant for onwind/offwind-*.
#
# The name patterns of each dataset (CUTOUT_DATASETS_ES) and the routing of cutout names to
# their dataset folder (cutout_folder_ES, used by input_cutout) live in rules/common.smk.
#

CUTOUT_IBERIA_ERA5_SARAH3_DATASET = dataset_version("cutout_iberia_era5_sarah3")


rule retrieve_cutout_iberia_era5_sarah3:
    """Retrieves ERA5-SARAH3 atlite cutouts for the Iberian Peninsula from Zenodo."""
    input:
        storage(CUTOUT_IBERIA_ERA5_SARAH3_DATASET["url"] + "/{cutout}.nc"),
    output:
        CUTOUT_IBERIA_ERA5_SARAH3_DATASET["folder"] + "/{cutout}.nc",
    wildcard_constraints:
        cutout=CUTOUT_DATASETS_ES["cutout_iberia_era5_sarah3"],
    log:
        "logs/retrieve_cutout_iberia_era5_sarah3/{cutout}.log",
    retries: 2
    resources:
        mem_mb=5000,
    run:
        copy2(input[0], output[0])


CUTOUT_IBERIA_CERRA_DATASET = dataset_version("cutout_iberia_cerra")


rule retrieve_cutout_iberia_cerra:
    """Retrieves CERRA wind-only atlite cutouts for the Iberian Peninsula from Zenodo."""
    input:
        storage(CUTOUT_IBERIA_CERRA_DATASET["url"] + "/{cutout}.nc"),
    output:
        CUTOUT_IBERIA_CERRA_DATASET["folder"] + "/{cutout}.nc",
    wildcard_constraints:
        cutout=CUTOUT_DATASETS_ES["cutout_iberia_cerra"],
    log:
        "logs/retrieve_cutout_iberia_cerra/{cutout}.log",
    retries: 2
    resources:
        mem_mb=5000,
    run:
        copy2(input[0], output[0])

#
#
#
####################



#################### Link weather cutouts from a local folder
#
# With pypsa_spain:local_cutouts enabled, a cutout found in its folder is symlinked into the
# dataset folder where the workflow expects it, instead of being retrieved or built. A cutout
# missing from that folder leaves link_local_cutout without input, so Snakemake falls back
# to the retrieve (or build) rule of its dataset.
#
# Snakemake rejects a ruleorder that names an undefined rule, hence the switch between
# retrieve_cutout and build_cutout, only one of which exists depending on data:cutout:source.
#

if (LOCAL_CUTOUTS := config["pypsa_spain"]["local_cutouts"])["enable"]:

    rule link_local_cutout:
        """Links a cutout from a local folder instead of retrieving or building it."""
        input:
            LOCAL_CUTOUTS["folder"] + "/{cutout}.nc",
        output:
            "{cutout_folder}/{cutout}.nc",
        wildcard_constraints:
            cutout_folder="|".join(
                re.escape(dataset["folder"])
                for dataset in [
                    CUTOUT_DATASET,
                    CUTOUT_IBERIA_ERA5_SARAH3_DATASET,
                    CUTOUT_IBERIA_CERRA_DATASET,
                ]
            ),
            cutout=r"[^/]+",
        localrule: True
        run:
            os.symlink(Path(input[0]).resolve(), output[0])

    ruleorder: link_local_cutout > retrieve_cutout_iberia_era5_sarah3
    ruleorder: link_local_cutout > retrieve_cutout_iberia_cerra

    if CUTOUT_DATASET["source"] == "build":

        ruleorder: link_local_cutout > build_cutout

    else:

        ruleorder: link_local_cutout > retrieve_cutout

#
#
#
####################
