from anndata import AnnData

from ehrapy.api.encode import Encoder
from ehrapy.api.io import DataReader


class Datasets:
    @staticmethod
    def mimic_2(encode: bool = False) -> AnnData:
        """Loads the MIMIC-II dataset

        Args:
            encode: Whether to return an already encoded object

        Returns:
            :class:`~anndata.AnnData` object of the MIMIC-II dataset
        """
        adata = DataReader.read(
            filename="ehrapy_mimic2.csv",
            backup_url="https://www.physionet.org/files/mimic2-iaccd/1.0/full_cohort_data.csv?download",
            suppress_warnings=True,
        )
        if encode:
            return Encoder.encode(adata, autodetect=True)

        return adata

    @staticmethod
    def mimic_3_demo(encode: bool = False) -> AnnData:
        """Loads the MIMIC-III demo dataset

        Args:
            encode: Whether to return an already encoded object

        Returns:
            :class:`~anndata.AnnData` object of the MIMIC-III demo Dataset
        """
        # adata = read(backupurl="https://physionet.org/static/published-projects/mimiciii-demo/mimic-iii-clinical-database-demo-1.4.zip")
        return None
