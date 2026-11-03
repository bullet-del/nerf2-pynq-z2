"""PC mock example used to verify the framework without a PYNQ board."""
from __future__ import annotations

import numpy as np


def prepare(overlay, allocate, contract, inputs):
    return {"scale": float(contract.get("adapter_config", {}).get("scale", 2.0))}


def run_once(context, inputs):
    return {"output": np.asarray(inputs["input"]) * context["scale"]}


def close(context):
    return None
