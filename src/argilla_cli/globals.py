from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GlobalState:
    json_output: bool = False
    verbose: bool = False


state = GlobalState()
