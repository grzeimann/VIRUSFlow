# Core models for VIRUSFlow
from .identity import ZipCode, RawFileId
# The deprecated legacy graph class is intentionally not re-exported here to satisfy the architecture gate;
# import from virusflow.core.graph if absolutely necessary during the deprecation window.
