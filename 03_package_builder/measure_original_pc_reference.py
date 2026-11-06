"""Measure the bundled original NeRF2 graph and original training checkpoints.

No fixed-Rx cache or factorized evaluator is used. A timing-only full test sweep
is independent of the three-repeat NVIDIA power experiment.
"""
from __future__ import annotations
import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path
import torch
from common import DEFAULT_CONFIG, KIT_ROOT, PC_TEMPLATE, SCENES, input_path, load_config, sha256, utc_now, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--scene', choices=(*SCENES, 'all'), default='all')
    parser.add_argument('--limit', type=int, default=0, help='0: complete split; positive: smoke only')
    parser.add_argument('--output-root', type=Path, default=KIT_ROOT/'work/full/pc_reference')
    args = parser.parse_args()
    if args.limit < 0: parser.error('limit cannot be negative')
    config = load_config(args.config)
    if not torch.cuda.is_available(): raise RuntimeError('CUDA is required')
    source = KIT_ROOT/'06_power_measurement/pc_gpu_benchmark.py'
    spec = importlib.util.spec_from_file_location('original_pc_benchmark', source)
    benchmark = importlib.util.module_from_spec(spec); spec.loader.exec_module(benchmark)
    source_root = Path(config['input_root'])
    if not source_root.is_absolute(): source_root = KIT_ROOT/source_root
    device = torch.device('cuda:0'); reports = []
    for scene in (SCENES if args.scene == 'all' else (args.scene,)):
        model = benchmark.load_scene(PC_TEMPLATE, scene, device, source_root)
        expected_hash = config['scenes'][scene]['checkpoint_sha256']
        if sha256(model['checkpoint_path']) != expected_hash:
            raise RuntimeError(scene + ': not the specified original checkpoint')
        context = benchmark.create_input_context(model['metadata_root'])
        all_ids = context['test_indices']; ids = all_ids[:args.limit or None]
        batch = int(model['config']['train']['batch_size'])
        benchmark.run_maps(model['renderer'], context, all_ids[:1], batch, device)
        result = benchmark.run_maps(model['renderer'], context, ids, batch, device, scene)
        report = {'schema': 'stage39-original-nerf2-pc-timing-v1', 'scene_id': scene,
            'created_utc': utc_now(), 'sample_count': len(ids), 'spectrum_ids': ids,
            'full_test_set': ids == all_ids, 'fixed_rx_cache_used': False,
            'original_nerf2_forward': True, 'checkpoint_sha256': expected_hash,
            'config_sha256': sha256(model['config_path']),
            'model_source_sha256': sha256(model['model_path']),
            'renderer_source_sha256': sha256(model['renderer_path']),
            'mean_end_to_end_seconds_per_spectrum': result['duration_s']/len(ids),
            'source': 'original NeRF2 model.py + renderer.py, original training checkpoint; fresh full-graph timing',
            'scope': 'CPU-to-GPU inputs, original FP32 render_ss, GPU synchronization and GPU-to-CPU output; excludes input construction, model load and plotting',
            'device': torch.cuda.get_device_name(device), 'batch_size': batch,
            'per_map': result['map_results'], 'status': 'PASS' if ids == all_ids else 'SMOKE_ONLY'}
        out = args.output_root/scene; write_json(out/'timing_reference.json', report)
        with (out/'per_map.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result['map_results'][0]))
            writer.writeheader(); writer.writerows(result['map_results'])
        reports.append({'scene': scene, 'maps': len(ids), 'status': report['status'],
                        'seconds_per_map': report['mean_end_to_end_seconds_per_spectrum']})
        print(json.dumps(reports[-1]), flush=True)
        del model; torch.cuda.empty_cache()
    write_json(args.output_root/'summary.json', {'schema': 'stage39-original-pc-timing-summary-v1',
        'status': 'PASS' if all(row['status']=='PASS' for row in reports) else 'SMOKE_ONLY',
        'scenes': reports, 'fixed_rx_cache_used': False})


if __name__ == '__main__': main()
