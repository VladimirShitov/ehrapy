"""Top-level package for ehrapy."""

__author__ = "Lukas Heumos"
__email__ = "lukas.heumos@posteo.net"
__version__ = "0.2.0"

from pypi_latest import PypiLatest
from rich import traceback

ehrapy_pypi_latest = PypiLatest("ehrapy", __version__)
ehrapy_pypi_latest.check_latest()

traceback.install(width=200, word_wrap=True)

from ehrapy._settings import EhrapyConfig, ehrapy_settings
from ehrapy._util import print_versions
from ehrapy.anndata_ext import anndata_to_df, df_to_anndata, type_overview

settings: EhrapyConfig = ehrapy_settings

from ehrapy import data as dt
from ehrapy import io
from ehrapy import plot as pl
from ehrapy import preprocessing as pp
from ehrapy import tools as tl
