"""Check original GPU-power inputs without sampling power or changing models."""
from __future__ import annotations
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--package-root', type=Path, default=ROOT/'outputs/N2-NeRF2-Stage39-PC-App')
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT/'03_package_builder'))
    from common import load_config, input_path, sha256
    spec = importlib.util.spec_from_file_location('power_input_check', ROOT/'06_power_measurement/pc_gpu_benchmark.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    import torch
    config = load_config()
    base = Path(config['input_root'])
    if not base.is_absolute(): base = ROOT/base
    for scene in config['scenes']:
        loaded = module.load_scene(args.package_root.resolve(), scene, torch.device('cpu'), base)
        expected = config['scenes'][scene]['checkpoint_sha256']
        if sha256(loaded['checkpoint_path']) != expected:
            raise RuntimeError(scene+': power benchmark selected another checkpoint')
        context = module.create_input_context(loaded['metadata_root'])
        if len(context['test_indices']) != config['scenes'][scene]['test_count']:
            raise RuntimeError(scene+': incorrect full-test coverage')
        print(json.dumps({'scene':scene, 'checkpoint':str(loaded['checkpoint_path']),
                          'source':str(loaded['model_path']), 'maps':len(context['test_indices']),
                          'status':'PASS'}, ensure_ascii=False))
    print('PC POWER INPUT CHECK PASS; no power experiment was performed.')

if __name__ == '__main__': main()
