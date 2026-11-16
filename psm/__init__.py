"""PC5 Platform Service Model (PSM): PC5-authored test doubles for the platform contract.

See ``docs/architecture/PS5_PLATFORM_CONTRACT.md`` for what these interfaces are
for, and :mod:`psm.psm` for the rule they all obey: an operation the contract
marks UNKNOWN returns ``UNSUPPORTED`` rather than a plausible fabrication.
"""

from .psm import (PSM_VERSION, Case, Clock, Interface, InterfaceInfo, OpSpec, PsmError,
                  Result, Status)

__all__ = ["PSM_VERSION", "Case", "Clock", "Interface", "InterfaceInfo", "OpSpec",
           "PsmError", "Result", "Status"]
