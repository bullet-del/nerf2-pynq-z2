#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run_verifier(root: Path) -> subprocess.CompletedProcess[str]:
    verifier = root / 'software/stage39/verify_stage39_board_package.py'
    return subprocess.run(
        [sys.executable, str(verifier)],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def require_success(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode:
        raise RuntimeError(f'{label} unexpectedly failed:\n{result.stdout}\n{result.stderr}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('package_root', type=Path)
    args = parser.parse_args()
    root = args.package_root.resolve()
    notebook_path = root / 'Stage39_QAT_Four_Scenes.ipynb'
    original = notebook_path.read_bytes()

    try:
        require_success(run_verifier(root), 'clean package verification')

        notebook = json.loads(original.decode('utf-8'))
        notebook['cells'][0]['execution_count'] = 37
        notebook['cells'][0]['outputs'] = [
            {'name': 'stdout', 'output_type': 'stream', 'text': ['simulated Jupyter output\n']}
        ]
        notebook['cells'][0]['metadata']['collapsed'] = False
        notebook['metadata']['widgets'] = {'application/vnd.jupyter.widget-state+json': {'state': {}}}
        notebook['cells'][0]['source'] = ''.join(notebook['cells'][0]['source'])
        notebook_path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        require_success(run_verifier(root), 'Jupyter runtime-state verification')

        notebook['cells'][0]['source'] += '\n# simulated source tamper\n'
        notebook_path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        tampered = run_verifier(root)
        combined = tampered.stdout + tampered.stderr
        if tampered.returncode == 0 or 'notebook source verification failed' not in combined:
            raise RuntimeError(
                'notebook source tamper was not rejected as expected:\n'
                + combined
            )
    finally:
        notebook_path.write_bytes(original)

    require_success(run_verifier(root), 'restored package verification')
    print('STAGE39 NOTEBOOK INTEGRITY REGRESSION PASS')


if __name__ == '__main__':
    main()
