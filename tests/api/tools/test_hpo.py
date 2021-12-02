import warnings
from math import isclose
from pathlib import Path

import numpy as np
import pandas as pd
from anndata import AnnData
from pandas import array
from pyhpo import HPOSet, HPOTerm, Ontology

from ehrapy.api.tools import HPO

CURRENT_DIR = Path(__file__).parent
_TEST_PATH = f"{CURRENT_DIR}/test_data_encode"

warnings.filterwarnings("ignore")


class TestHPO:
    def setup_method(self):
        _ = Ontology()

        obs_data = {"disease": ["Lumbar scoliosis", "Neuroblastoma"]}
        var_data = {"values": ["not required"]}
        self.test_adata = AnnData(
            X=np.array(np.random.rand(2, 1)),
            obs=pd.DataFrame(data=obs_data),
            var=pd.DataFrame(data=var_data),
            dtype=np.dtype(object),
        )

    def test_patient_hpo_similarity(self):
        patient_1 = HPOSet.from_queries(["HP:0002943", "HP:0008458", "HP:0100884", "HP:0002944", "HP:0002751"])
        patient_2 = HPOSet.from_queries(["HP:0002650", "HP:0010674", "HP:0000925", "HP:0009121"])
        similarity = HPO.patient_hpo_similarity(patient_1, patient_2)

        assert isclose(similarity, 0.7583849517696274)

    def test_hpo_term_similarity(self):
        term_1 = Ontology.get_hpo_object("Scoliosis")
        term_2 = Ontology.get_hpo_object("Abnormal axial skeleton morphology")
        path = HPO.hpo_term_similarity(term_1, term_2)

        assert path == (
            3,
            (
                HPOTerm(
                    id="HP:0002650",
                    name="Scoliosis",
                    is_a=["HP:0010674 ! Abnormality of the curvature of the vertebral column"],
                ),
                HPOTerm(
                    id="HP:0010674",
                    name="Abnormality of the curvature of the vertebral column",
                    is_a=["HP:0000925 ! Abnormality of the vertebral column"],
                ),
                HPOTerm(
                    id="HP:0000925",
                    name="Abnormality of the vertebral column",
                    is_a=["HP:0009121 ! Abnormal axial skeleton morphology"],
                ),
                HPOTerm(
                    id="HP:0009121",
                    name="Abnormal axial skeleton morphology",
                    is_a=["HP:0011842 ! Abnormality of skeletal morphology"],
                ),
            ),
            3,
            0,
        )

    def test_closest_hpo_term_strict_anndata(self):
        HPO.map_to_hpo(self.test_adata, obs_key="disease", strict=True)

        assert "Lumbar scoliosis" in self.test_adata.obs["hpo_terms"].values
        assert "Neuroblastoma" in self.test_adata.obs["hpo_terms"].values

    def test_closest_hpo_term_anndata(self):
        HPO.map_to_hpo(self.test_adata, obs_key="disease", strict=False)

        assert (
            array(
                [
                    ["Thoracolumbar scoliosis", "Lumbar scoliosis"],
                    [
                        "Neuroblastoma",
                        "Congenital neuroblastoma",
                        "Ganglioneuroblastoma",
                        "Localized neuroblastoma",
                        "Olfactory esthesioneuroblastoma",
                    ],
                ],
                dtype=object,
            )
            in self.test_adata.obs["hpo_terms"].values
        )

        # HP: 0003006 | Neuroblastoma
        # HP: 0006742 | Congenital neuroblastoma
        # HP: 0006747 | Ganglioneuroblastoma
        # HP: 0006768 | Localized neuroblastoma
        # HP: 0030068 | Olfactory esthesioneuroblastoma
