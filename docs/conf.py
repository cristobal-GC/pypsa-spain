# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'pypsa-spain'
copyright = '2024, Cristobal Gallego-Castillo'
author = 'Cristobal Gallego-Castillo'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinxcontrib.bibtex",
]

bibtex_bibfiles = ["bib_github_pypsa_spain.bib"]
bibtex_default_style = "unsrt"

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

#html_theme = 'alabaster'
html_theme = "sphinx_book_theme"

html_static_path = ['_static']

##### PyPSA-Spain: project logo in the sidebar brand area. It replaces the project
##### title, which sphinx-book-theme keeps as the alt text of the image.
##### Its size is capped in _static/custom.css so it stays discreet.
html_logo = "img/Logo_PyPSA-Spain.png"
html_css_files = ["custom.css"]
