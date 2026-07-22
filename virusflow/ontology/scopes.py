from __future__ import annotations

from enum import Enum


class PhysicalScope(str, Enum):
    PIXEL = "pixel"
    FIBER = "fiber"
    AMPLIFIER = "amplifier"
    PHYSICAL_CCD = "physical_ccd"
    SPECTROGRAPH = "spectrograph"
    IFU = "ifu"
    EXPOSURE = "exposure"
    DITHER_SET = "dither_set"
    OBSERVATION = "observation"
    OBSERVATION_SET = "observation_set"
    INSTRUMENT_EPOCH = "instrument_epoch"

