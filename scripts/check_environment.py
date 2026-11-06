"""Check the dependencies actually imported by the cold reproduction pipeline."""
from __future__ import annotations
import argparse
import importlib
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-cpu', action='store_true')
    args = parser.parse_args()
    rows = []; errors = []
    for name in ('torch', 'torchvision', 'numpy', 'pandas', 'PIL', 'imageio',
                 'yaml', 'scipy', 'skimage', 'matplotlib', 'configargparse', 'tqdm', 'einops'):
        try:
            module = importlib.import_module(name)
            rows.append({'module': name, 'version': str(getattr(module, '__version__', 'installed'))})
        except Exception as exc:
            errors.append(name + ': ' + str(exc))
    if not errors:
        import torch
        if not args.allow_cpu and not torch.cuda.is_available():
            errors.append('CUDA is required for the formal model and PC reference stages')
    print(json.dumps({'status': 'FAIL' if errors else 'PASS', 'python': sys.version,
                      'dependencies': rows, 'errors': errors}, indent=2))
    if errors: raise SystemExit(1)
    print('ENVIRONMENT CHECK PASS')


if __name__ == '__main__': main()
