"""Reject partial, cached or mismatched PC timing before it reaches the App."""
from __future__ import annotations
import json
import math
from pathlib import Path


def require_original_pc_timing(path, checkpoint_sha256, expected_ids):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    rows=value.get('per_map',[])
    valid=(value.get('schema')=='stage39-original-nerf2-pc-timing-v1'
        and value.get('status')=='PASS' and value.get('full_test_set') is True
        and value.get('original_nerf2_forward') is True and value.get('fixed_rx_cache_used') is False
        and value.get('checkpoint_sha256')==checkpoint_sha256
        and value.get('spectrum_ids')==expected_ids and value.get('sample_count')==len(expected_ids)
        and len(rows)==len(expected_ids) and len(rows)>0)
    if valid:
        valid=all(row.get('spectrum_id')==sid and row.get('test_ordinal')==i
            and math.isfinite(float(row['duration_s'])) and float(row['duration_s'])>0
            for i,(sid,row) in enumerate(zip(expected_ids,rows)))
        mean=float(value['mean_end_to_end_seconds_per_spectrum'])
        valid=valid and math.isfinite(mean) and mean>0 and math.isclose(
            mean,sum(float(row['duration_s']) for row in rows)/len(rows),rel_tol=1e-12)
    if not valid: raise RuntimeError('Complete original-NeRF2 full-graph PC timing is required: '+str(path))
    return value
