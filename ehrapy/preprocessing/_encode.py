from __future__ import annotations

from collections import defaultdict
from itertools import chain
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from _collections import OrderedDict
from anndata import AnnData
from category_encoders import CountEncoder, HashingEncoder
from mudata import MuData
from rich import print
from rich.progress import BarColumn, Progress
from sklearn.preprocessing import LabelEncoder, OneHotEncoder

from ehrapy import logging as logg
from ehrapy.anndata.anndata_ext import _update_uns

multi_encoding_modes = {"hash_encoding"}
available_encodings = {"one_hot_encoding", "label_encoding", "count_encoding", *multi_encoding_modes}


def encode(
    data: AnnData | MuData,
    autodetect: bool | dict = False,
    encodings: dict[str, dict[str, list[str]]] | dict[str, list[str]] | str | None = None,
) -> AnnData | None:
    """Encode the initial read :class:`~anndata.AnnData` or :class:`~mudata.MuData` object.

    Categorical values could be either passed via parameters or are autodetected on the fly.
    The categorical values are also stored in obs and uns (for keeping the original, unencoded values).
    The current encoding modes for each variable are also stored in uns (`var_to_encoding` key).
    Variable names in var are updated according to the encoding modes used. A variable name starting with `ehrapycat_`
    indicates an encoded column (or part of it).

    Autodetect mode:
        This can be used for convenience and when there are many columns that need to be encoded. Note that missing values do not influence the result.
        By using this mode, every column that contains non-numerical values is encoded. In addition, every binary
        column will be encoded too. These are those columns which contain only 1's and 0's (could be either integers or floats).

    Available encodings are:
        1. one_hot_encoding (https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.OneHotEncoder.html)
        2. label_encoding (https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.LabelEncoder.html)
        3. count_encoding (https://contrib.scikit-learn.org/category_encoders/count.html)
        4. hash_encoding (https://contrib.scikit-learn.org/category_encoders/hashing.html)

    Args:
        data: The initial :class:`~anndata.AnnData` or :class:`~mudata.MuData` object
        autodetect: Whether to autodetect categorical values that will be encoded.
        encodings: Only needed if autodetect set to False (or False for some columns in case of a :class:`~mudata.MuData` object).
        A dict containing the encoding mode and categorical name for the respective column (for each AnnData object in case of MuData object).

    Returns:
        An :class:`~anndata.AnnData` object with the encoded values in X or None (in case of :class:`~mudata.MuData` object).

    Examples:
        >>> import ehrapy as ep
        >>> adata = ep.io.read(...)
        >>> # encode all autodetected (non numerical) columns using label encoding
        >>> adata_encoded = ep.pp.encode(adata, autodetect=True)

    # Example using autodetect with non-default encoding mode:
        >>> import ehrapy as ep
        >>> adata = ep.io.read(...)
        >>> # encode all autodetected (non numerical) columns using one hot encoding (this only works for single column encoding modes, not hash encoding)
        >>> adata_encoded = ep.pp.encode(adata, autodetect=True, 'one_hot_encoding')

    # Example using custom encodings per columns:
        >>> import ehrapy as ep
        >>> adata = ep.io.read(...)
        >>> # encode col1 and col2 using label encoding and encode col3 using one hot encoding
        >>> adata_encoded = ep.pp.encode(adata, autodetect=False, {'label_encoding': ['col1', 'col2'], 'one_hot_encoding': ['col3']})
    """
    if isinstance(data, AnnData):
        # basic type checking for passed parameters when encoding a single AnnData object
        if not _check_anndata_input_type(autodetect, encodings):
            raise EncodingInputValueError
        return _encode(adata=data, autodetect=autodetect, encodings=encodings)
    elif isinstance(data, MuData):
        # basic type checking for passed parameters when encoding a MuData object (collection of multiple AnnData objects)
        if not _check_mudata_input_type(autodetect, encodings):
            raise EncodingInputValueError
        for adata in data.mod.keys():
            detect, encodings_modes = _get_mudata_autodetect_options_and_encoding_modes(adata, autodetect, encodings)  # type: ignore
            # autodetect is set to False, but no encodings were provided; warn and skip this object
            if not detect and not encodings_modes:
                print(
                    f"[bold yellow]Skipped encoding modality {adata}, as autodetect is set to false and no encodings were provided!"
                )
            if encodings_modes:
                data.mod[adata] = _encode(data.mod[adata], detect, encodings_modes)
            data.update()
            # no need to return since this references the original MuData object
    else:
        print(f"[b red]Cannot encode object of type {type(data)}. Can only encode AnnData or MuData objects!")
        raise ValueError
    if isinstance(data, AnnData):
        logg.debug("Encoded the AnnData object.")
    elif isinstance(data, MuData):
        logg.debug("Encoded the MuData object.")

    return None


def undo_encoding(
    data: AnnData | MuData,
    columns: str = "all",
) -> AnnData | None:
    """Undo the current encodings applied to all columns in X.

    This currently resets the AnnData or MuData object to its initial state.

    Args:
        data: The :class:`~anndata.AnnData` or MuData object
        columns: The names of the columns to reset encoding for. Defaults to all columns.

    Returns:
        A (partially) encoding reset :class:`~anndata.AnnData` or MuData object

    Examples:
        >>> import ehrapy as ep
        >>> # adata_encoded is an encoded AnnData object
        >>> adata_undone = ep.encode.undo_encoding(adata_encoded)
    """
    if isinstance(data, AnnData):
        return _undo_encoding(data, columns)
    elif isinstance(data, MuData):
        for adata in data.mod.keys():
            reset_adata = _undo_encoding(data.mod[adata], columns)
            if reset_adata:
                data.mod[adata] = reset_adata
        data.update()
    else:
        print(f"[b red]Cannot decode object of type {type(data)}. Can only decode AnnData or MuData objects!")
        raise ValueError
    if isinstance(data, AnnData):
        logg.debug("Decoded the AnnData object.")
    elif isinstance(data, MuData):
        logg.debug("Decoded the MuData object.")

    return None


def _encode(
    adata: AnnData,
    autodetect: bool | dict = False,
    encodings: dict[str, dict[str, list[str]]] | dict[str, list[str]] | str = None,
) -> AnnData | None:
    """Encode the initial read AnnData object. Categorical values could be either passed via parameters or autodetected.

    Label encodes by default which is used to save initially unencoded AnnData objects.

    Args:
        adata: The inital AnnData object parsed by the :class: DataReader
        autodetect: Autodetection of categorical values (default: False)
        encodings: Only needed if autodetect set to False. A dict containing the categorical name
        and the encoding mode for the respective column

    Returns:
        An :class:`~anndata.AnnData` object with the encoded values in X
    """
    # check whether original layer exists; if not add it
    if "original" not in adata.layers.keys():
        adata.layers["original"] = adata.X.copy()
    # autodetect categorical values, which could lead to more categoricals
    if autodetect:
        if "var_to_encoding" in adata.uns.keys():
            print(
                "[bold yellow]The current AnnData object has been already encoded. Returning original AnnData object!"
            )
            return adata

        categoricals_names = adata.uns["non_numerical_columns"]
        # no columns were detected, that would require an encoding (e.g. non numerical columns)
        if not categoricals_names:
            print("[bold yellow]Detected no columns that need to be encoded. Leaving passed AnnData object unchanged.")
            return adata
        # copy uns so it can be used in encoding process without mutating the original anndata object
        orig_uns_copy = adata.uns.copy()
        _add_categoricals_to_uns(adata, orig_uns_copy, categoricals_names)

        encoded_x = None
        encoded_var_names = adata.var_names.to_list()
        encode_mode = encodings if encodings is not None else "label_encoding"
        if encode_mode not in available_encodings - multi_encoding_modes:
            print(
                f"Unknown encoding mode {encode_mode}. Please provide one of the following encoding modes:\n"
                f"{available_encodings - multi_encoding_modes}"
            )
            raise ValueError
        single_encode_mode_switcher = {
            "one_hot_encoding": _one_hot_encoding,
            "label_encoding": _label_encoding,
            "count_encoding": _count_encoding,
        }
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            refresh_per_second=1500,
        ) as progress:
            task = progress.add_task(f"[red]Running {encode_mode} on detected columns ...", total=1)
            # encode using the desired mode
            encoded_x, encoded_var_names = single_encode_mode_switcher[encode_mode](  # type: ignore
                adata,
                encoded_x,
                orig_uns_copy,
                encoded_var_names,
                categoricals_names,
                progress,
                task,
            )
            progress.update(task, description="Updating layer originals ...")
            # update layer content with the latest categorical encoding and the old other values
            logg.info("Encoding strings in X to save to .h5ad. Loading the file will reverse the encoding.")
            updated_layer = _update_layer_after_encoding(
                adata.layers["original"],
                encoded_x,
                encoded_var_names,
                adata.var_names.to_list(),
                categoricals_names,
            )
            progress.update(task, description=f"[bold blue]Finished {encode_mode} of autodetected columns.")

            encoded_ann_data = AnnData(
                encoded_x,
                obs=adata.obs.copy(),
                var=dict(var_names=encoded_var_names),
                uns=orig_uns_copy,
                layers={"original": updated_layer},
                dtype=np.float32,
            )
            encoded_ann_data.uns["var_to_encoding"] = {categorical: encode_mode for categorical in categoricals_names}
            encoded_ann_data.uns["encoding_to_var"] = {encode_mode: categoricals_names}

            encoded_ann_data.uns["numerical_columns"] = adata.uns["numerical_columns"].copy()
            encoded_ann_data.uns["non_numerical_columns"] = []

            _add_categoricals_to_obs(adata, encoded_ann_data, categoricals_names)

    # user passed categorical values with encoding mode for each of them
    else:
        # Required since this would be deleted through side references
        non_numericals = adata.uns["non_numerical_columns"].copy()
        # reencode data
        if "var_to_encoding" in adata.uns.keys():
            encodings = _reorder_encodings(adata, encodings)  # type: ignore
            adata = _undo_encoding(adata, "all")
        adata.uns["non_numerical_columns"] = non_numericals
        # are all specified encodings valid?
        for encoding_mode in encodings.keys():  # type: ignore
            if encoding_mode not in available_encodings:
                print(
                    f"Unknown encoding mode {encoding_mode}. Please provide one of the following encoding modes:\n"
                    f"{available_encodings}"
                )
                raise ValueError
        adata.uns["encoding_to_var"] = encodings

        categoricals_not_flat = list(chain(*encodings.values()))  # type: ignore
        # this is needed since multi column encoding will get passed a list of list instead of a flat list
        categoricals = list(
            chain(
                *(
                    _categoricals if isinstance(_categoricals, list) else (_categoricals,)
                    for _categoricals in categoricals_not_flat
                )
            )
        )
        # ensure no categorical column gets encoded twice
        if len(categoricals) != len(set(categoricals)):
            raise ValueError(
                "The categorical column names given contain at least one duplicate column. "
                "Check the column names to ensure that no column is encoded twice!"
            )
        elif any(cat in adata.uns["numerical_columns"] for cat in categoricals):
            print(
                "[bold yellow]At least one of passed column names seems to have numerical dtype. In general it is not recommended "
                "to encode numerical columns!"
            )
        orig_uns_copy = adata.uns.copy()
        _add_categoricals_to_uns(adata, orig_uns_copy, categoricals)
        var_to_encoding = {} if "var_to_encoding" not in adata.uns.keys() else adata.uns["var_to_encoding"]
        encoded_x = None
        encoded_var_names = adata.var_names.to_list()
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            refresh_per_second=1500,
        ) as progress:
            for encoding_mode in encodings.keys():  # type: ignore
                task = progress.add_task(f"[red]Setting up {encoding_mode}", total=1)
                encode_mode_switcher = {
                    "one_hot_encoding": _one_hot_encoding,
                    "label_encoding": _label_encoding,
                    "count_encoding": _count_encoding,
                    "hash_encoding": _hash_encoding,
                }
                progress.update(task, description=f"Running {encoding_mode} ...")
                # perform the actual encoding
                encoded_x, encoded_var_names = encode_mode_switcher[encoding_mode](adata, encoded_x, orig_uns_copy, encoded_var_names, encodings[encoding_mode], progress, task)  # type: ignore
                # update encoding history in uns
                for categorical in encodings[encoding_mode]:  # type: ignore
                    # multi column encoding modes -> multiple encoded columns
                    if isinstance(categorical, list):
                        for column_name in categorical:
                            var_to_encoding[column_name] = encoding_mode
                    else:
                        var_to_encoding[categorical] = encoding_mode

        # update original layer content with the new categorical encoding and the old other values
        updated_layer = _update_layer_after_encoding(
            adata.layers["original"],
            encoded_x,
            encoded_var_names,
            adata.var_names.to_list(),
            categoricals,
        )
        try:
            encoded_ann_data = AnnData(
                encoded_x,
                obs=adata.obs.copy(),
                var=dict(var_names=encoded_var_names),
                uns=orig_uns_copy,
                layers={"original": updated_layer},
                dtype=np.float32,
            )
            # update current encodings in uns
            encoded_ann_data.uns["var_to_encoding"] = var_to_encoding

        # if the user did not pass every non numerical column for encoding, a Anndata object cannot be created
        except ValueError:
            print(
                "[bold red]Creation of AnnData object failed. Ensure that you passed all non numerical, "
                "categorical values for encoding!"
            )
            raise AnnDataCreationError
        updated_num_uns, updated_non_num_uns, _ = _update_uns(adata, categoricals)
        encoded_ann_data.uns["numerical_columns"] = updated_num_uns
        encoded_ann_data.uns["non_numerical_columns"] = updated_non_num_uns

        _add_categoricals_to_obs(adata, encoded_ann_data, categoricals)

    logg.debug("Successfully encoded the AnnData object.")

    return encoded_ann_data


def _one_hot_encoding(
    adata: AnnData,
    X: np.ndarray | None,
    uns: dict[str, Any],
    var_names: list[str],
    categories: list[str],
    progress: Progress,
    task,
) -> tuple[np.ndarray, list[str]]:
    """Encode categorical columns using one hot encoding.

    Args:
        adata: The current AnnData object
        X: Current (encoded) X
        uns: A copy of the original uns
        var_names: Var names of current AnnData object
        categories: The name of the categorical columns to be encoded

    Returns:
        Encoded new X and the corresponding new var names
    """
    original_values = _initial_encoding(uns, categories)
    progress.update(task, description="[blue]Running one hot encoding on passed columns ...")
    encoder = OneHotEncoder(handle_unknown="ignore", sparse=False).fit(original_values)
    categorical_prefixes = [
        f"ehrapycat_{category}_{str(suffix).strip()}"
        for idx, category in enumerate(categories)
        for suffix in encoder.categories_[idx]
    ]
    transformed = encoder.transform(original_values)
    # X is None if this is the first encoding "round" -> take the former X
    if X is None:
        X = adata.X
    progress.advance(task, 1)
    progress.update(task, description="[blue]Updating X and var ...")

    temp_x, temp_var_names = _update_encoded_data(X, transformed, var_names, categorical_prefixes, categories)
    progress.update(task, description="[blue]Finished one hot encoding.")

    return temp_x, temp_var_names


def _label_encoding(
    adata: AnnData,
    X: np.ndarray | None,
    uns: dict[str, Any],
    var_names: list[str],
    categoricals: list[str],
    progress: Progress,
    task,
) -> tuple[np.ndarray, list[str]]:
    """Encode categorical columns using label encoding.

    Args:
        adata: The current AnnData object
        X: Current (encoded) X
        uns: A copy of the original uns
        var_names: Var names of current AnnData object
        categoricals: The name of the categorical columns, that need to be encoded

    Returns:
        Encoded new X and the corresponding new var names
    """
    original_values = _initial_encoding(uns, categoricals)
    # label encoding expects input array to be 1D, so iterate over all columns and encode them one by one
    for idx in range(original_values.shape[1]):
        progress.update(task, description=f"[blue]Running label encoding on column {categoricals[idx]} ...")
        label_encoder = LabelEncoder()
        row_vec = original_values[:, idx : idx + 1].ravel()  # type: ignore
        label_encoder.fit(row_vec)
        transformed = label_encoder.transform(row_vec)
        # need a column vector instead of row vector
        original_values[:, idx : idx + 1] = transformed[..., None]
        progress.advance(task, 1 / len(categoricals))
    category_prefixes = [f"ehrapycat_{categorical}" for categorical in categoricals]
    # X is None if this is the first encoding "round" -> take the former X
    if X is None:
        X = adata.X

    progress.update(task, description="[blue]Updating X and var ...")
    temp_x, temp_var_names = _update_encoded_data(X, original_values, var_names, category_prefixes, categoricals)
    progress.update(task, description="[blue]Finished label encoding.")

    return temp_x, temp_var_names


def _count_encoding(
    adata: AnnData,
    X: np.ndarray | None,
    uns: dict[str, Any],
    var_names: list[str],
    categoricals: list[str],
    progress: Progress,
    task,
) -> tuple[np.ndarray, list[str]]:
    """Encode categorical column using count encoding.

    Args:
        adata: The current AnnData object
        X: Current (encoded) X
        uns: A copy of the original uns
        var_names: Var names of current AnnData object
        categoricals: The name of the categorical columns, that need to be encoded

    Returns:
        Encoded new X and the corresponding new var names
    """
    original_values = _initial_encoding(uns, categoricals)
    progress.update(task, description="[blue]Running count encoding encoding on passed columns ...")
    # returns a pandas dataframe per default, but numpy array is needed
    count_encoder = CountEncoder(return_df=False)
    count_encoder.fit(original_values)
    category_prefix = [f"ehrapycat_{categorical}" for categorical in categoricals]
    transformed = count_encoder.transform(original_values)
    # X is None if this is the first encoding "round" -> take the former X
    if X is None:
        X = adata.X  # noqa: N806

    progress.advance(task, 1)
    progress.update(task, description="[blue]Updating X and var ...")
    temp_x, temp_var_names = _update_encoded_data(X, transformed, var_names, category_prefix, categoricals)
    progress.update(task, description="[blue]Finished count encoding.")

    return temp_x, temp_var_names


def _hash_encoding(
    adata: AnnData,
    X: np.ndarray | None,
    uns: dict[str, Any],
    var_names: list[str],
    categories: list[list[str]],
    progress: Progress,
    task,
) -> tuple[np.ndarray, list[str]]:
    """Encode categorical columns using hash encoding.

    Args:
        adata: The current AnnData object
        X: Current (encoded) X
        uns: A copy of the original uns
        var_names: Var names of current AnnData object
        categories: The name of the categorical columns to be encoded

    Returns:
        Encoded new X and the corresponding new var names
    """
    transformed_all, encoded_var_names = None, []
    for idx, multi_columns in enumerate(categories):
        progress.update(task, description=f"Running hash encoding on {idx + 1}. list ...")
        original_values = _initial_encoding(uns, multi_columns)

        encoder = HashingEncoder(return_df=False, n_components=8).fit(original_values)
        encoded_var_names += [f"ehrapycat_hash_{multi_columns[0]}" for _ in range(8)]
        transformed = encoder.transform(original_values)
        transformed_all = np.hstack((transformed_all, transformed)) if transformed_all is not None else transformed
        progress.advance(task, 1 / len(categories))

    # X is None if this is the first encoding "round" -> take the former X
    if X is None:
        X = adata.X
    progress.update(task, description="[blue]Updating X and var ...")

    temp_x, temp_var_names = _update_multi_encoded_data(
        X, transformed_all, var_names, encoded_var_names, sum(categories, [])
    )
    if temp_x.shape[1] != len(temp_var_names):
        raise HashEncodingError(
            "Hash encoding of input data failed. Note that hash encoding is not "
            "suitable for datasets with low number of data points and low cardinality!"
        )
    progress.update(task, description="[blue]Finished hash encoding.")

    return temp_x, temp_var_names


def _update_layer_after_encoding(
    old_layer: np.ndarray,
    new_x: np.ndarray,
    new_var_names: list[str],
    old_var_names: list[str],
    categories: list[str],
) -> np.ndarray:
    """Update the original layer containing the initial non categorical values and the latest encoded categoricals.

    Args:
        old_layer: The previous "original" layer
        new_x: The new encoded X
        new_var_names: The new encoded var names
        old_var_names: The previous var names
        categories: All previous categorical names

    Returns
        A Numpy array containing all numericals together with all encoded categoricals.
    """
    try:
        # get the index of the first column of the new encoded X, that does not store an encoded categorical
        new_cat_stop_index = next(i for i in range(len(new_var_names)) if not new_var_names[i].startswith("ehrapycat"))
        # get the index of the first column of the old encoded X, that does not store an encoded categorical
        old_cat_stop_index = next(i for i in range(len(old_var_names)) if not old_var_names[i].startswith("ehrapycat"))
    # when there are only encoded columns, simply return a copy of the new X, since to originals will be kept in the layer
    except StopIteration:
        return new_x.copy().astype("float32")
    # keep track of all indices with original value columns, that are (and were) not encoded
    idx_list = []
    for idx, col_name in enumerate(old_var_names[old_cat_stop_index:]):
        # this case is needed when there are one or more numerical (but categorical) columns that was not encoded yet
        if col_name not in categories:
            idx_list.append(idx + old_cat_stop_index)
    # slice old original layer using the selector
    old_layer_view = old_layer[:, idx_list]
    # get all encoded categoricals of X
    encoded_categoricals = new_x[:, :new_cat_stop_index]
    # horizontally stack all encoded categoricals and the remaining "old original values"
    updated_layer = np.hstack((encoded_categoricals, old_layer_view))

    try:
        logg.info("Updated the original layer after encoding.")
        return updated_layer.astype("float32")
    except ValueError as e:
        raise ValueError("Ensure that all columns which require encoding are being encoded.") from e


def _update_multi_encoded_data(
    X: np.ndarray,
    transformed: np.ndarray,
    var_names: list[str],
    encoded_var_names: list[str],
    categoricals: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Update X and var_names after applying multi column encoding modes to some columns

    Args:
        X: Current (former) X
        transformed: The encoded (transformed) categorical columns
        var_names: Var names of current AnnData object
        encoded_var_names: The name(s) of the encoded column(s)
        categoricals: The categorical values that were encoded recently

    Returns:
        Encoded new X and the corresponding new var names
    """
    idx = []
    for pos, name in enumerate(var_names):
        if name in categoricals:
            idx.append(pos)
    # delete the original categorical column
    del_cat_column_x = np.delete(X, list(idx), 1)
    # create the new, encoded X
    temp_x = np.hstack((transformed, del_cat_column_x))
    # delete old categorical name
    var_names = [col_name for col_idx, col_name in enumerate(var_names) if col_idx not in idx]
    temp_var_names = encoded_var_names + var_names

    logg.debug("Updated `X` and `var_names` after applying multi column encoding modes.")

    return temp_x, temp_var_names


def _update_encoded_data(
    X: np.ndarray,
    transformed: np.ndarray,
    var_names: list[str],
    categorical_prefixes: list[str],
    categoricals: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Update X and var_names after each encoding.

    Args:
        X: Current (former) X
        transformed: The encoded (transformed) categorical column
        var_names: Var names of current AnnData object
        categorical_prefixes: The name(s) of the encoded column(s)
        categoricals: The categorical values that were encoded recently

    Returns:
        Encoded new X and the corresponding new var names
    """
    idx = _get_categoricals_old_indices(var_names, categoricals)
    # delete the original categorical column
    del_cat_column_x = np.delete(X, list(idx), 1)
    # create the new, encoded X
    temp_x = np.hstack((transformed, del_cat_column_x))
    # delete old categorical name
    var_names = [col_name for col_idx, col_name in enumerate(var_names) if col_idx not in idx]
    temp_var_names = categorical_prefixes + var_names

    logg.debug("Updated `X` and `var_names` after each encoding.")

    return temp_x, temp_var_names


def _initial_encoding(
    uns: dict[str, Any],
    categoricals: list[str],
) -> np.ndarray:
    """Get all original values for all categoricals that need to be encoded (again).

    Args:
        uns: A copy of the original AnnData object's uns
        categoricals: All categoricals that need to be encoded

    Returns:
        Numpy array of all original categorial values
    """
    uns_: dict[str, np.ndarray] = uns
    # create numpy array from all original categorical values, that will be encoded (again)
    array = np.array(
        [uns_["original_values_categoricals"][categoricals[i]].ravel() for i in range(len(categoricals))]
    ).transpose()

    return array


def _undo_encoding(
    adata: AnnData,
    columns: str = "all",
    suppress_warning: bool = False,
) -> AnnData | None:
    """
    Undo the current encodings applied to all columns in X. This currently resets the AnnData object to its initial state.

    Args:
        adata: The AnnData object
        columns: The names of the columns to reset encoding for. Defaults to all columns. This resets the AnnData object to its initial state.
        suppress_warning: Whether warnings should be suppressed or not (only True if called from a MuData object)

    Returns:
        A (partially) encoding reset AnnData object
    """
    if "var_to_encoding" not in adata.uns.keys():
        if not suppress_warning:
            print("[bold yellow]Calling undo_encoding on unencoded AnnData object.")
        return None

    # get all encoded variables
    encoded_categoricals = list(adata.uns["original_values_categoricals"].keys())
    # get all columns that should be stored in obs only
    columns_obs_only = [
        column_name for column_name in list(adata.obs.columns) if column_name not in encoded_categoricals
    ]

    if columns == "all":
        categoricals = list(adata.uns["original_values_categoricals"].keys())
    else:
        print("[bold yellow]Currently, one can only reset encodings for all columns! [bold red]Aborting...")
        return None
    transformed = _initial_encoding(adata.uns, categoricals)
    temp_x, temp_var_names = _delete_all_encodings(adata)
    new_x = np.hstack((transformed, temp_x)) if temp_x is not None else transformed
    new_var_names = categoricals + temp_var_names if temp_var_names is not None else categoricals
    # only keep columns in obs that were stored in obs only -> delete every encoded column from obs
    new_obs = adata.obs[columns_obs_only]
    uns = OrderedDict()
    # reset uns and keep numerical/non-numerical columns
    num_vars, non_num_vars = adata.uns["numerical_columns"], adata.uns["non_numerical_columns"]
    for cat in categoricals:
        original_values = adata.uns["original_values_categoricals"][cat]
        type_first_nan = original_values[np.where(original_values != np.nan)][0]
        if isinstance(type_first_nan, (int, float, complex)) and not isinstance(type_first_nan, bool):
            num_vars.append(cat)
        else:
            non_num_vars.append(cat)

    uns["numerical_columns"] = num_vars
    uns["non_numerical_columns"] = non_num_vars

    logg.debug("Encoding of the AnnData object was reset.")

    return AnnData(
        new_x,
        obs=new_obs,
        var=pd.DataFrame(index=new_var_names),
        uns=uns,
        dtype=np.dtype(object),
        layers={"original": new_x.copy()},
    )


def _delete_all_encodings(adata: AnnData) -> tuple[np.ndarray | None, list | None]:
    """Delete all encoded columns and keep track of their indices.

    Args:
        adata: The AnnData object to operate on

    Returns:
        A temporary X were all encoded columns are deleted and all var_names of unencoded columns.
    """
    var_names = list(adata.var_names)
    if adata.X is not None and var_names is not None:
        idx = 0
        for var in var_names:
            if not var.startswith("ehrapycat"):
                break
            idx += 1
        # case: only encoded columns were found
        if idx == len(var_names):
            return None, None
        # don't need to consider case when no encoded columns are there, since undo_encoding would not run anyways in this case
        logg.info("All encoded columns of the AnnData object were deleted.")
        return adata.X[:, idx:].copy(), var_names[idx:]
    return None, None


def _reorder_encodings(adata: AnnData, new_encodings: dict[str, list[list[str]] | list[str]]):
    """Reorder the encodings and update which column will be encoded using which mode (with which columns in case of multi column encoding modes).

    Args:
        adata: The AnnData object to be reencoded
        new_encodings: The new encodings passed by the user (might affect encoded as well as previously non encoded columns)

    Returns:
        An updated encoding scheme
    """
    flattened_modes: list[list[str] | str] = sum(new_encodings.values(), [])  # type: ignore
    latest_encoded_columns = list(chain(*(i if isinstance(i, list) else (i,) for i in flattened_modes)))
    # check for duplicates and raise an error if any
    if len(set(latest_encoded_columns)) != len(latest_encoded_columns):
        print(
            "[bold red]Reencoding AnnData object failed. You have at least one duplicate in your encodings. A column "
            "cannot be encoded at the same time using different encoding modes!"
        )
        raise DuplicateColumnEncodingError
    old_encode_mode = adata.uns["var_to_encoding"]
    for categorical in latest_encoded_columns:
        encode_mode = old_encode_mode.get(categorical)
        # if None, this categorical has not been encoded before but will be encoded now for the first time
        # multi column encoding mode
        if encode_mode in multi_encoding_modes:
            encoded_categoricals_with_mode = adata.uns["encoding_to_var"][encode_mode]
            _found = False
            for column_list in encoded_categoricals_with_mode:
                for column_name in column_list:
                    if column_name == categorical:
                        column_list.remove(column_name)
                        _found = True
                        break
                # a categorical can only be encoded once and therefore found once
                if _found:
                    break
            # filter all lists that are now empty since all variables will be reencoded from this list
            updated_multi_list = filter(None, encoded_categoricals_with_mode)
            # if no columns remain that will be encoded with this encode mode, delete this mode from modes as well
            if not list(updated_multi_list):
                del adata.uns["encoding_to_var"][encode_mode]
        # single column encoding mode
        elif encode_mode in available_encodings:
            encoded_categoricals_with_mode = adata.uns["encoding_to_var"][encode_mode]
            for ind, column_name in enumerate(encoded_categoricals_with_mode):
                if column_name == categorical:
                    del encoded_categoricals_with_mode[ind]
                    break
                # if encoding mode is
            if not encoded_categoricals_with_mode:
                del adata.uns["encoding_to_var"][encode_mode]
    logg.info("Re-encoded the AnnData object.")
    # return updated encodings
    return _update_new_encode_modes(new_encodings, adata.uns["encoding_to_var"])


def _update_new_encode_modes(
    new_encodings: dict[str, list[list[str]] | list[str]],
    filtered_old_encodings: dict[str, list[list[str]] | list[str]],
):
    """Update the encoding scheme.

    If the encoding mode exists in the filtered old encodings, append all values (columns that should be encoded using this mode) to this key.
    If not, defaultdict ensures that no KeyError will be raised and the values are simply appended to the default value ([]).

    Args:
        new_encodings: The new encoding modes passed by the user; basically what will be passed for encodings when calling the encode API
        filtered_old_encodings: The old encoding modes, but with all columns removed that will be reencoded

    Returns:
        The updated encoding scheme
    """
    updated_encodings = defaultdict(list)  # type: ignore
    for k, v in chain(new_encodings.items(), filtered_old_encodings.items()):
        updated_encodings[k] += v

    logg.debug("Updated the encoding scheme.")

    return dict(updated_encodings)


def _get_categoricals_old_indices(old_var_names: list[str], encoded_categories: list[str]) -> set[int]:
    """Get indices of every (possibly encoded) categorical column belonging to a newly encoded categorical value.

    Args:
        old_var_names: Former variables names
        encoded_categories: Already encoded categories

    Returns:
        Set of all indices of formerly encoded categories belonging to a newly encoded categorical value.
    """
    idx_list = set()
    category_set = set(encoded_categories)
    for idx, old_var_name in enumerate(old_var_names):
        # if the old variable was previously unencoded (only the case for numerical categoricals)
        if old_var_name in category_set:
            idx_list.add(idx)
        # if the old variable was already encoded
        elif old_var_name.startswith("ehrapycat_"):
            if any(old_var_name[10:].startswith(category) for category in category_set):
                idx_list.add(idx)

    return idx_list


def _add_categoricals_to_obs(original: AnnData, new: AnnData, categorical_names: list[str]) -> None:
    """Add the original categorical values to obs.

    Args:
        original: The original AnnData object
        new: The new AnnData object
        categorical_names: Name of each categorical column
    """
    for idx, var_name in enumerate(original.var_names):
        if var_name in new.obs.columns:
            continue
        elif var_name in categorical_names:
            new.obs[var_name] = original.X[::, idx : idx + 1].flatten()
            # note: this will count binary columns (0 and 1 only) as well
            # needed for writing to .h5ad files
            if set(pd.unique(new.obs[var_name])).issubset({False, True, np.NaN}):
                new.obs[var_name] = new.obs[var_name].astype("bool")
    # get all non bool object columns and cast the to category dtype
    object_columns = list(new.obs.select_dtypes(include="object").columns)
    new.obs[object_columns] = new.obs[object_columns].astype("category")
    logg.info(f"The original categorical values `{categorical_names}` were added to obs.")


def _add_categoricals_to_uns(original: AnnData, new: AnnData, categorical_names: list[str]) -> None:
    """Add the original categorical values to uns.

    Args:
        original: The original AnnData object
        new: The new AnnData object
        categorical_names: Name of each categorical column
    """
    is_initial = "original_values_categoricals" in original.uns.keys()
    new["original_values_categoricals"] = {} if not is_initial else original.uns["original_values_categoricals"].copy()

    for idx, var_name in enumerate(original.var_names):
        if is_initial and var_name in new["original_values_categoricals"]:
            continue
        elif var_name in categorical_names:
            # keep numerical dtype when writing original values to uns
            if var_name in original.uns["numerical_columns"]:
                new["original_values_categoricals"][var_name] = original.X[::, idx : idx + 1].astype("float")
            else:
                new["original_values_categoricals"][var_name] = original.X[::, idx : idx + 1].astype("str")

    logg.info(f"The original categorical values `{categorical_names}` were added to uns.")


def _get_mudata_autodetect_options_and_encoding_modes(
    identifier: str, autodetect: dict, encodings: dict[str, dict[str, list[str]]]
) -> tuple[bool, dict | None]:
    """
    Extract the index column (if any) and the columns, for obs only (if any) from the given user input.

    This function is only called when dealing with datasets consisting of multiple files (for example MIMIC-III).

    For each file, `index_columns` and `columns_obs_only` can provide three cases:
        1.) The filename (thus the identifier) is not present as a key and no default key is provided or one or both dicts are empty:
            --> No index column will be set and/or no columns are obs only (based on user input)

            .. code-block:: python
                   # some setup code here
                   ...
                   # filename
                   identifier1 = "MyFile"
                   identifier2 = "MyOtherFile"
                   # no default key and identifier1 is not in the index or columns_obs_only keys
                   # -> no index column will be set and no columns will be obs only (except datetime, if any)
                   index_columns = {"MyOtherFile":["MyOtherColumn1"]}
                   columns_obs_only = {"MyOtherFile":["MyOtherColumn2"]}

        2.) The filename (thus the identifier) is not present as a key, but default key is provided
            --> The index column will be set and/or columns will be obs only according to the default key

            .. code-block:: python
                  # some setup code here
                   ...
                   # filename
                   identifier1 = "MyFile"
                   identifier2 = "MyOtherFile"
                   # identifier1 is not in the index or columns_obs_only keys, but default key is set for both
                   # -> index column will be set using MyColumn1 and column obs only will include MyColumn2
                   index_columns = {"MyOtherFile":["MyOtherColumn1"], "default": "MyColumn1"}
                   columns_obs_only = {"MyOtherFile":["MyOtherColumn2"], "default": "MyColumn2"}

        3.) The filename is present as a key
            --> The index column will be set and/or columns are obs only according to its value

            .. code-block:: python
                   # some setup code here
                   ...
                   # filename
                   identifier1 = "MyFile"
                   identifier2 = "MyOtherFile"
                   # identifier1 is in the index and columns_obs_only keys
                   # -> index column will be MyColumn1 and columns_obs_only will include MyColumn2 and MyColumn3
                   index_columns = {"MyFile":["MyColumn1"]}
                   columns_obs_only = {"MyFile":["MyColumn2", "MyColumn3"]}

    Args:
        identifier: The name of the file
        autodetect: A Dictionary of files to autodetected categorical columns
        encodings: A Dictionary from mapping to columns

    Returns:
        Index column (if any) and columns obs only (if any) for this specific AnnData object
    """
    _autodetect = False
    _encodings = None
    # should use autodetect on this object?
    if identifier in autodetect:
        _autodetect = autodetect[identifier]
    elif "default" in autodetect:
        _autodetect = autodetect["default"]

    # get encodings (if autodetection is not used)
    if not _autodetect:
        if identifier in encodings:
            _encodings = encodings[identifier]

    return _autodetect, _encodings


def _check_anndata_input_type(
    autodetect: bool | dict, encodings: dict[str, dict[str, list[str]]] | dict[str, list[str]] | str | None
) -> bool:
    """
    Check type of passed parameters, whether they match the requirements to encode an AnnData object or not.

    Args:
        autodetect: Whether columns, that should be encoded, should be autodetected or not

    Returns:
        Whether they match type requirements or not
    """
    if not isinstance(autodetect, bool):
        print(
            f"[bold red]Attempted to encode an AnnData object, but passed parameter for [bold blue]autodetect [bold red]{autodetect} is not a boolean."
            f"Please provide a boolean value for [bold blue]autodetect [bold red]when encoding a single AnnData object!"
        )
        return False
    elif isinstance(encodings, str) and not autodetect:
        print("[bold red]Passing a string for parameter encodings is only possible when using autodetect=True!")
        return False
    elif autodetect and not isinstance(encodings, (str, type(None))):
        print(
            "[bold red]Setting encode mode when autodetect=True only works by passing a string (encode mode name) or None not {type(encodings)}!"
        )
        return False

    return True


def _check_mudata_input_type(
    autodetect: bool | dict, encodings: dict[str, dict[str, list[str]]] | dict[str, list[str]] | str | None
) -> bool:
    """Check type of passed parameters, whether they match the requirements to encode a MuData object or not.

    Args:
        autodetect: Whether columns, that should be encoded, should be autodetected or not
        encodings: (Different) encoding mode(s) and their columns to be applied on

    Returns:
        Whether they match type requirements or not
    """
    if not isinstance(autodetect, Dict):
        print(
            f"[bold red]Tried encoding a MuData object, but passed parameter for [bold blue]autodetect [bold red]{autodetect} is not a dictionary. "
            f"Please provide a dictionary for [bold blue]autodetect [bold red]when encoding a MuData object!"
        )
        return False
    elif encodings and any(isinstance(column, List) for column in encodings.values()):  # type: ignore
        print(
            "[bold red]Encoding a MuData object requires a dictionary passed for every AnnData object, that should be encoded, containing the "
            "encoding modes and columns, as required for a single AnnData object!"
        )
        return False
    return True


class AlreadyEncodedWarning(UserWarning):
    pass


class EncodingInputValueError(ValueError):
    pass


class AnnDataCreationError(ValueError):
    pass


class DuplicateColumnEncodingError(ValueError):
    pass


class HashEncodingError(Exception):
    pass
