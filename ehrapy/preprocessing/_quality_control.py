from __future__ import annotations

from pathlib import Path
from typing import Collection, Literal

import numpy as np
import pandas as pd
from anndata import AnnData

from ehrapy.core.str_matching import StrMatcher


def qc_metrics(
    adata: AnnData, qc_vars: Collection[str] = (), layer: str = None, inplace: bool = True
) -> pd.DataFrame | None:
    """Calculates various quality control metrics.

    Uses the original values to calculate the metrics and not the encoded ones.
    Look at the return type for a more in depth description of the calculated metrics.

    Args:
        adata: Annotated data matrix.
        qc_vars: Optional List of vars to calculate additional metrics for.
        layer: Layer to use to calculate the metrics.
        inplace: Whether to add the metrics to obs/var or to solely return a Pandas DataFrame.

    Returns:
        Pandas DataFrame of all calculated QC metrics.

        Observation level metrics include:

        `missing_values_abs`
            Absolute amount of missing values.
        `missing_values_pct`
            Relative amount of missing values in percent.

        Feature level metrics include:

        `missing_values_abs`
            Absolute amount of missing values.
        `missing_values_pct`
            Relative amount of missing values in percent.
        `mean`
            Mean value of the features.
        `median`
            Median value of the features.
        `std`
            Standard deviation of the features.
        `min`
            Minimum value of the features.
        `max`
            Maximum value of the features.

        Example:
            .. code-block:: python

                import ehrapy as ep
                import seaborn as sns
                import matplotlib.pyplot as plt

                adata = ep.dt.mimic_2(encode=True)
                ep.pp.calculate_qc_metrics(adata)
                sns.displot(adata.obs["missing_values_abs"])
                plt.show()
    """
    obs_metrics = _obs_qc_metrics(adata, layer, qc_vars)
    var_metrics = _var_qc_metrics(adata, layer)

    if inplace:
        adata.obs[obs_metrics.columns] = obs_metrics
        adata.var[var_metrics.columns] = var_metrics

    return obs_metrics, var_metrics


def _missing_values(
    arr: np.ndarray, shape: tuple[int, int] = None, df_type: Literal["obs", "var"] = "obs"
) -> np.ndarray:
    """Calculates the absolute or relative amount of missing values.

    Args:
        arr: Numpy array containing a data row which is a subset of X (mtx).
        shape: Shape of X (mtx).
        df_type: Whether to calculate the proportions for obs or var. One of 'obs' or 'var' (default: 'obs').

    Returns:
        Absolute or relative amount of missing values.
    """
    # Absolute number of missing values
    if shape is None:
        return pd.isnull(arr).sum()
    # Relative number of missing values in percent
    else:
        n_rows, n_cols = shape
        if df_type == "obs":
            return (pd.isnull(arr).sum() / n_cols) * 100
        else:
            return (pd.isnull(arr).sum() / n_rows) * 100


def _obs_qc_metrics(
    adata: AnnData, layer: str = None, qc_vars: Collection[str] = (), log1p: bool = True
) -> pd.DataFrame:
    """Calculates quality control metrics for observations.

    See :func:`~ehrapy.preprocessing._quality_control.calculate_qc_metrics` for a list of calculated metrics.

    Args:
        adata: Annotated data matrix.
        layer: Layer containing the actual data matrix.
        qc_vars: A list of previously calculated QC metrics to calculate summary statistics for.
        log1p: Whether to apply log1p normalization for the QC metrics. Only used with parameter 'qc_vars'.

    Returns:
        A Pandas DataFrame with the calculated metrics.
    """
    obs_metrics = pd.DataFrame(index=adata.obs_names)
    mtx = adata.X if layer is None else adata.layers[layer]

    obs_metrics["missing_values_abs"] = np.apply_along_axis(_missing_values, 1, mtx)
    obs_metrics["missing_values_pct"] = np.apply_along_axis(_missing_values, 1, mtx, shape=mtx.shape, df_type="obs")

    # Specific QC metrics
    for qc_var in qc_vars:
        obs_metrics[f"total_features_{qc_var}"] = np.ravel(mtx[:, adata.var[qc_var].values].sum(axis=1))
        if log1p:
            obs_metrics[f"log1p_total_features_{qc_var}"] = np.log1p(obs_metrics[f"total_features_{qc_var}"])
        obs_metrics["total_features"] = np.ravel(mtx.sum(axis=1))
        obs_metrics[f"pct_features_{qc_var}"] = (
            obs_metrics[f"total_features_{qc_var}"] / obs_metrics["total_features"] * 100
        )

    return obs_metrics


def _var_qc_metrics(adata: AnnData, layer: str = None) -> pd.DataFrame:
    """Calculates quality control metrics for features.

    See :func:`~ehrapy.preprocessing._quality_control.calculate_qc_metrics` for a list of calculated metrics.

    Args:
        adata: Annotated data matrix.
        layer: Layer containing the matrix to calculate the metrics for.

    Returns:
        Pandas DataFrame with the calculated metrics.
    """
    # TODO we need to ensure that we are calculating the QC metrics for the original -> look at adata.uns
    var_metrics = pd.DataFrame(index=adata.var_names)
    mtx = adata.X if layer is None else adata.layers[layer]

    var_metrics["missing_values_abs"] = np.apply_along_axis(_missing_values, 0, mtx)
    var_metrics["missing_values_pct"] = np.apply_along_axis(_missing_values, 0, mtx, shape=mtx.shape, df_type="var")
    try:
        var_metrics["mean"] = mtx.mean(axis=0)
        var_metrics["median"] = np.median(mtx, axis=0)
        var_metrics["standard_deviation"] = mtx.std(axis=0)
        var_metrics["min"] = mtx.min(axis=0)
        var_metrics["max"] = mtx.max(axis=0)
    except TypeError:
        var_metrics["mean"] = np.nan
        var_metrics["median"] = np.nan
        var_metrics["standard_deviation"] = np.nan
        var_metrics["min"] = np.nan
        var_metrics["max"] = np.nan

    return var_metrics


def qc_lab_measurements(
    adata: AnnData,
    reference_table: pd.DataFrame = None,
    measurements: list[str] = None,
    unit: Literal["traditional", "SI"] = "SI",
    layer: str = None,
    threshold: float = 0.2,
    age_col: str = "age",
    sex_col: str = "sex",
    ethnicity_col: str = "race",
    copy: bool = False,
) -> AnnData:
    """Examines lab measurements for reference ranges and outliers.

    Source:
        The used reference values were obtained from https://accessmedicine.mhmedical.com/content.aspx?bookid=1069&sectionid=60775149 .
        This table is compiled from data in the following sources:

        * Tietz NW, ed. Clinical Guide to Laboratory Tests. 3rd ed. Philadelphia: WB Saunders Co; 1995;
        * Laposata M. SI Unit Conversion Guide. Boston: NEJM Books; 1992;
        * American Medical Association Manual of Style: A Guide for Authors and Editors. 9th ed. Chicago: AMA; 1998:486–503. Copyright 1998, American Medical Association;
        * Jacobs DS, DeMott WR, Oxley DK, eds. Jacobs & DeMott Laboratory Test Handbook With Key Word Index. 5th ed. Hudson, OH: Lexi-Comp Inc; 2001;
        * Henry JB, ed. Clinical Diagnosis and Management by Laboratory Methods. 20th ed. Philadelphia: WB Saunders Co; 2001;
        * Kratz A, et al. Laboratory reference values. N Engl J Med. 2006;351:1548–1563; 7) Burtis CA, ed. Tietz Textbook of Clinical Chemistry and Molecular Diagnostics. 5th ed. St. Louis: Elsevier; 2012.

        This version of the table of reference ranges was reviewed and updated by Jessica Franco-Colon, PhD, and Kay Brooks.

    Limitations:
        * Reference ranges differ between continents, countries and even laboratories (https://informatics.bmj.com/content/28/1/e100419).
          The default values used here are only one of many options.
        * Ensure that the values used as input are provided with the correct units. We recommend the usage of SI values.
        * The reference values pertain to adults. Many of the reference ranges need to be adapted for children.
        * By default if no gender is provided and no unisex values are available, we use the **male** reference ranges.
        * The used reference ranges may be biased for ethnicity. Please examine the primary sources if required.

    Args:
        adata: Annotated data matrix.
        reference_table: A custom DataFrame with reference values. Defaults to the laposata table if not specified.
        measurements: A list of measurements to check.
        unit: The unit of the measurements. (default: SI)
        layer: Layer containing the matrix to calculate the metrics for.
        threshold: Minimum required matching confidence score of the bigrams.
                   0 = low requirements, 1 = high requirements.
        age_col: Column containing age values.
        sex_col: Column containing sex values.
        ethnicity_col: Column containing ethnicity values.
        copy: Whether to return a copy (default: False).

    Returns:
        A modified AnnData object (copy if specified).

    Example:
        .. code-block:: python

            import ehrapy as ep

            adata = ep.dt.mimic_2(encode=True)
            ep.pp.lab_measurements_qc(adata)
    """
    if copy:
        adata = adata.copy()

    preprocessing_dir = Path(__file__).parent.resolve()
    if reference_table is None:
        reference_table = pd.read_csv(
            f"{preprocessing_dir}/laboratory_reference_tables/laposata.tsv", sep="\t", index_col="Measurement"
        )

    str_matcher = StrMatcher(list(reference_table.index))

    for column in measurements:
        score, best_column_match = str_matcher.best_match(query=column, threshold=threshold)
        reference_column = "SI Reference Interval" if unit == "SI" else "Traditional Reference Interval"

        reference_values = reference_table.loc[best_column_match, reference_column]
        if layer is not None:
            actual_measurements = adata[:, column].layers[layer]
        else:
            actual_measurements = adata[:, column].X

        print(reference_values)
        print(actual_measurements)

    # check for sex, age, ethnicity etc

    # check for the type of check (< - or >)

    # get the results

    # adapt var with the results

    return adata
