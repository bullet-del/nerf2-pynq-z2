"""Reusable AXI DMA stream adapter with explicit cache maintenance.

Expected adapter_config fields:
  dma_ip, input_key, output_key, output_shape, output_dtype
"""
from __future__ import annotations

import numpy as np


def prepare(overlay, allocate, contract, inputs):
    config = contract["adapter_config"]
    source = np.asarray(inputs[config.get("input_key", "input")])
    output_shape = tuple(config.get("output_shape", source.shape))
    output_dtype = np.dtype(config.get("output_dtype", str(source.dtype)))
    return {
        "dma": getattr(overlay, config["dma_ip"]),
        "input": allocate(shape=source.shape, dtype=source.dtype),
        "output": allocate(shape=output_shape, dtype=output_dtype),
        "input_key": config.get("input_key", "input"),
        "output_key": config.get("output_key", "output"),
    }


def run_once(context, inputs):
    np.copyto(context["input"], np.asarray(inputs[context["input_key"]]))
    if hasattr(context["input"], "flush"):
        context["input"].flush()
    context["output"].fill(0)
    if hasattr(context["output"], "flush"):
        context["output"].flush()
    dma = context["dma"]
    dma.recvchannel.transfer(context["output"])
    dma.sendchannel.transfer(context["input"])
    dma.sendchannel.wait()
    dma.recvchannel.wait()
    if hasattr(context["output"], "invalidate"):
        context["output"].invalidate()
    return {context["output_key"]: np.asarray(context["output"]).copy()}


def close(context):
    for name in ("input", "output"):
        value = context.get(name)
        if hasattr(value, "freebuffer"):
            value.freebuffer()
