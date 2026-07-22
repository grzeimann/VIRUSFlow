from __future__ import annotations

"""Explicit catalog provider boundary for exposure astrometry."""

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np
from astropy.io import ascii
from astropy.table import Table


class CatalogProvider(Protocol):
    name: str
    version: str

    def cone_search(self, ra: float, dec: float, radius_deg: float) -> Table:
        ...


@dataclass(frozen=True)
class PanSTARRSCSVProvider:
    """Pan-STARRS DR2 CSV provider following the robust STScI API pattern."""

    table: str = "stack"
    release: str = "dr2"
    baseurl: str = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs"
    timeout_seconds: float = 60.0
    name: str = "panstarrs_stsci_csv"
    version: str = "ps1-dr2-stack-csv-2"

    def cone_search(self, ra: float, dec: float, radius_deg: float) -> Table:
        import requests

        # The DR2 ``stack`` schema calls these gPSFMag/gPSFMagErr.  Normalize
        # them below so callers consume one provider-independent contract.
        columns = ["objID", "raMean", "decMean", "gPSFMag", "gPSFMagErr", "qualityFlag"]
        params = {
            "ra": float(ra), "dec": float(dec), "radius": float(radius_deg),
            "columns": "[" + ",".join(columns) + "]",
        }
        response = requests.get(
            f"{self.baseurl}/{self.release}/{self.table}.csv",
            params=params, timeout=float(self.timeout_seconds),
        )
        response.raise_for_status()
        if not response.text.strip():
            return Table(names=["objID", "raMean", "decMean", "gMeanPSFMag", "gMeanPSFMagErr", "qualityFlag"])
        table = ascii.read(response.text, format="csv", guess=False)
        table.rename_column("gPSFMag", "gMeanPSFMag")
        table.rename_column("gPSFMagErr", "gMeanPSFMagErr")
        for column in table.colnames:
            if table[column].dtype.kind in "fi":
                values = np.asarray(table[column])
                mask = values <= -999.0
                if np.any(mask):
                    table[column][mask] = np.nan
        return table


@dataclass(frozen=True)
class FixtureCatalogProvider:
    rows: Sequence[Sequence[float]]
    name: str = "deterministic_fixture"
    version: str = "fixture-1"

    def cone_search(self, ra: float, dec: float, radius_deg: float) -> Table:  # noqa: ARG002
        values = np.asarray(self.rows, dtype=float).reshape((-1, 3))
        return Table(
            {"objID": np.arange(values.shape[0]), "raMean": values[:, 0], "decMean": values[:, 1],
             "gMeanPSFMag": values[:, 2], "gMeanPSFMagErr": np.full(values.shape[0], 0.01),
             "qualityFlag": np.zeros(values.shape[0], dtype=int)}
        )
