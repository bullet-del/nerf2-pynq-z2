#!/usr/bin/env python3
"""Hashed five-row packet files and dynamic-Tx patching for Stage34."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from stage34_stream import COLS, WORDS_PER_PAIR, pack_encoded_row, run_header


ROWS = 90
ROWS_PER_PACKET = 5
PACKET_COUNT = ROWS // ROWS_PER_PACKET
MAX_RAYS_PER_PACKET = ROWS_PER_PACKET * COLS
MAX_WORDS_PER_PACKET = 1 + (MAX_RAYS_PER_PACKET // 2) * WORDS_PER_PAIR
MAX_PACKET_BYTES = MAX_WORDS_PER_PACKET * 8


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def combine_packed_rows(rows):
    rows = [np.asarray(value, dtype=np.uint64).reshape(-1) for value in rows]
    if not rows or len(rows) > ROWS_PER_PACKET:
        raise ValueError("Stage34 packet must contain 1..5 rows")
    row_words = 1 + (COLS // 2) * WORDS_PER_PAIR
    if any(value.size != row_words for value in rows):
        raise ValueError("Stage34 packed row length changed")
    nrays = len(rows) * COLS
    result = np.empty(1 + len(rows) * (row_words - 1), dtype=np.uint64)
    result[0] = run_header(nrays)
    offset = 1
    for value in rows:
        result[offset:offset + row_words - 1] = value[1:]
        offset += row_words - 1
    if result.nbytes >= (1 << 23):
        raise AssertionError("Stage34 five-row packet exceeds AXI DMA length field")
    return result


def pack_encoded_rows(encoded_rows):
    return combine_packed_rows([pack_encoded_row(value) for value in encoded_rows])


def tx_words(tx_q):
    value = np.asarray(tx_q, dtype=np.int8).reshape(63)
    padded = np.zeros(64, dtype=np.int8)
    padded[:63] = value
    return np.ascontiguousarray(padded).view("<u8").reshape(8)


def patch_tx_inplace(packet, tx_q):
    packet = np.asarray(packet, dtype=np.uint64).reshape(-1)
    nrays = int(int(packet[0]) & 0x00ffffff)
    expected = 1 + (nrays // 2) * WORDS_PER_PAIR
    if nrays < 2 or nrays % 2 or packet.size != expected:
        raise ValueError("invalid Stage34 packet header or length")
    records = packet[1:].reshape(nrays // 2, WORDS_PER_PAIR)
    words = tx_words(tx_q)
    records[:, 8:16] = words
    records[:, 24:32] = words
    return packet


class PackedPacketSource:
    def __init__(self, manifest_path, packets_dir):
        self.manifest_path = Path(manifest_path)
        self.packets_dir = Path(packets_dir)
        if not self.manifest_path.is_file():
            raise FileNotFoundError(self.manifest_path)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if (self.manifest.get("schema") != "n2-stage34-five-row-packets-v1" or
                int(self.manifest.get("rows_per_packet", 0)) != ROWS_PER_PACKET or
                int(self.manifest.get("packet_count", 0)) != PACKET_COUNT):
            raise RuntimeError("unsupported Stage34 packet manifest")
        self.entries = list(self.manifest["packets"])
        if len(self.entries) != PACKET_COUNT:
            raise RuntimeError("Stage34 packet manifest is incomplete")
        self.manifest_sha256 = sha256_file(self.manifest_path)

    def load(self, index, tx_q=None):
        entry = self.entries[int(index)]
        path = self.packets_dir / entry["file"]
        if not path.is_file() or path.stat().st_size != int(entry["file_bytes"]):
            raise FileNotFoundError("Stage34 packet is missing or truncated: %s" % path)
        if sha256_file(path) != entry["sha256"]:
            raise RuntimeError("Stage34 packet hash failed: %s" % path)
        value = np.load(path, allow_pickle=False)
        value = np.array(value, dtype=np.uint64, copy=True).reshape(-1)
        if value.size != int(entry["stream_words"]):
            raise RuntimeError("Stage34 packet word count changed: %s" % path)
        nrays = int(entry["ray_count"])
        if int(int(value[0]) & 0x00ffffff) != nrays:
            raise RuntimeError("Stage34 packet header changed: %s" % path)
        if tx_q is not None:
            patch_tx_inplace(value, tx_q)
        return value, nrays, entry


__all__ = [
    "ROWS_PER_PACKET", "PACKET_COUNT", "MAX_RAYS_PER_PACKET",
    "MAX_WORDS_PER_PACKET", "MAX_PACKET_BYTES", "PackedPacketSource",
    "combine_packed_rows", "pack_encoded_rows", "patch_tx_inplace", "tx_words",
]
