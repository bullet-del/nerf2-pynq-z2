#!/usr/bin/env python3
"""Hashed Stage35 signal constants and exact fixed-Rx cache packet packing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from stage34_packets import PackedPacketSource, sha256_file, tx_words


ROWS, COLS, SAMPLES = 90, 360, 64
# The signed source files remain five-row packets because an exact, uncached
# point packet is already 7.60 MB (near the 23-bit AXI-DMA limit).  Cached
# inference contains only constants + 64 cached uint16 values, so four adjacent
# source packets can safely be merged into a 20-row DMA transaction.
SOURCE_ROWS_PER_PACKET = 5
SOURCE_PACKET_COUNT = ROWS // SOURCE_ROWS_PER_PACKET
FAST_SOURCE_PACKETS_PER_TRANSFER = 4
ROWS_PER_PACKET = SOURCE_ROWS_PER_PACKET * FAST_SOURCE_PACKETS_PER_TRANSFER
PACKET_COUNT = (SOURCE_PACKET_COUNT + FAST_SOURCE_PACKETS_PER_TRANSFER - 1) // FAST_SOURCE_PACKETS_PER_TRANSFER
CACHE_PACKET_COUNT = SOURCE_PACKET_COUNT
CACHE_MAX_RAYS_PER_PACKET = SOURCE_ROWS_PER_PACKET * COLS
MAX_RAYS_PER_PACKET = ROWS_PER_PACKET * COLS
FULL_WORDS_PER_PAIR = 1056
FAST_WORDS_PER_PAIR = 32 + SAMPLES
MAX_FAST_WORDS = 1 + (MAX_RAYS_PER_PACKET // 2) * FAST_WORDS_PER_PAIR
MAX_CACHE_OUTPUT_WORDS = (CACHE_MAX_RAYS_PER_PACKET // 2) * SAMPLES
OP_CACHED = 5
OP_BUILD_CACHE = 6


def command_header(op, rays):
    rays = int(rays)
    if op not in (OP_CACHED, OP_BUILD_CACHE) or rays < 2 or rays % 2:
        raise ValueError("invalid Stage35 command or ray count")
    return np.uint64((int(op) << 60) | rays)


class SignalConstantSource:
    def __init__(self, manifest_path, packets_dir):
        self.manifest_path = Path(manifest_path)
        self.packets_dir = Path(packets_dir)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if (self.manifest.get("schema") != "n2-stage35-signal-constant-packets-v1" or
                int(self.manifest.get("packet_count", 0)) != SOURCE_PACKET_COUNT or
                int(self.manifest.get("rows_per_packet", 0)) != SOURCE_ROWS_PER_PACKET):
            raise RuntimeError("unsupported Stage35 constant manifest")
        self.entries = list(self.manifest["packets"])
        if len(self.entries) != SOURCE_PACKET_COUNT:
            raise RuntimeError("Stage35 constant packet list is incomplete")
        self.manifest_sha256 = sha256_file(self.manifest_path)

    def load(self, index, tx_q=None):
        entry = self.entries[int(index)]
        path = self.packets_dir / entry["file"]
        if (not path.is_file() or path.stat().st_size != int(entry["file_bytes"]) or
                sha256_file(path) != entry["sha256"]):
            raise RuntimeError("Stage35 constant packet integrity failed: %s" % path)
        value = np.array(np.load(path, allow_pickle=False), dtype=np.uint64, copy=True)
        pairs = int(entry["pair_count"])
        if value.shape != (pairs, 32):
            raise RuntimeError("Stage35 constant packet shape changed: %s" % path)
        if tx_q is not None:
            words = tx_words(tx_q)
            value[:, 8:16] = words
            value[:, 24:32] = words
        return value, entry

    def load_group(self, index, tx_q=None):
        """Merge up to four contiguous five-row sources into one fast packet."""
        index = int(index)
        begin = index * FAST_SOURCE_PACKETS_PER_TRANSFER
        end = min(begin + FAST_SOURCE_PACKETS_PER_TRANSFER, SOURCE_PACKET_COUNT)
        if index < 0 or begin >= SOURCE_PACKET_COUNT:
            raise IndexError(index)
        values, entries = [], []
        for source_index in range(begin, end):
            value, entry = self.load(source_index, tx_q=tx_q)
            values.append(value); entries.append(entry)
        first_row = int(entries[0]["first_row"])
        row_count = sum(int(item["row_count"]) for item in entries)
        ray_count = sum(int(item["ray_count"]) for item in entries)
        if first_row != begin * SOURCE_ROWS_PER_PACKET or ray_count != row_count * COLS:
            raise RuntimeError("Stage35 grouped packet rows are not contiguous")
        return np.concatenate(values, axis=0), {
            "packet_index": index,
            "first_row": first_row,
            "row_count": row_count,
            "ray_count": ray_count,
            "source_packet_begin": begin,
            "source_packet_end_exclusive": end,
        }


def pack_fast_packet(constants, attenuation, ray_count):
    ray_count = int(ray_count)
    constants = np.asarray(constants, dtype=np.uint64)
    attenuation = np.asarray(attenuation, dtype=np.uint16).reshape(ray_count, SAMPLES)
    pairs = ray_count // 2
    if constants.shape != (pairs, 32):
        raise ValueError("Stage35 constants have wrong shape")
    paired = attenuation.reshape(pairs, 2, SAMPLES).astype(np.uint64)
    cache_words = paired[:, 0, :] | (paired[:, 1, :] << np.uint64(16))
    records = np.empty((pairs, FAST_WORDS_PER_PAIR), dtype=np.uint64)
    records[:, :32] = constants
    records[:, 32:] = cache_words
    result = np.empty(1 + records.size, dtype=np.uint64)
    result[0] = command_header(OP_CACHED, ray_count)
    result[1:] = records.reshape(-1)
    if result.size > MAX_FAST_WORDS:
        raise AssertionError("Stage35 fast packet exceeds fixed buffer")
    return result


def cache_build_packet(source, index):
    packed, nrays, entry = source.load(index)
    packed[0] = command_header(OP_BUILD_CACHE, nrays)
    return packed, nrays, entry


def decode_cache_words(words, ray_count):
    ray_count = int(ray_count)
    words = np.asarray(words, dtype=np.uint64).reshape(ray_count // 2, SAMPLES)
    result = np.empty((ray_count // 2, 2, SAMPLES), dtype=np.uint16)
    result[:, 0, :] = (words & np.uint64(0xffff)).astype(np.uint16)
    result[:, 1, :] = ((words >> np.uint64(16)) & np.uint64(0xffff)).astype(np.uint16)
    return result.reshape(ray_count, SAMPLES)


__all__ = [
    "ROWS", "COLS", "SAMPLES", "ROWS_PER_PACKET", "PACKET_COUNT",
    "SOURCE_ROWS_PER_PACKET", "SOURCE_PACKET_COUNT", "CACHE_PACKET_COUNT",
    "FAST_SOURCE_PACKETS_PER_TRANSFER", "CACHE_MAX_RAYS_PER_PACKET",
    "MAX_RAYS_PER_PACKET", "MAX_FAST_WORDS", "MAX_CACHE_OUTPUT_WORDS",
    "SignalConstantSource", "PackedPacketSource", "pack_fast_packet",
    "cache_build_packet", "decode_cache_words", "sha256_file",
]
