#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'stage39_board_package_manifest.json'
SCENES = ('s23', 'Meetingroom', 'Office1', 'Office2')
PACKAGE_REVISION = 'stage39-board-cold-repro-20261012-r1'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def notebook_source_sha(path):
    """Hash executable/document source while ignoring Jupyter runtime state."""
    try:
        notebook = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Stage39 notebook cannot be read: {path.name}: {exc}') from exc
    if notebook.get('nbformat') != 4 or not isinstance(notebook.get('cells'), list):
        raise RuntimeError(f'Stage39 notebook structure is invalid: {path.name}')

    cells = []
    for index, cell in enumerate(notebook['cells']):
        if not isinstance(cell, dict) or not isinstance(cell.get('cell_type'), str):
            raise RuntimeError(f'Stage39 notebook cell {index} is invalid: {path.name}')
        source = cell.get('source', '')
        if isinstance(source, list):
            if not all(isinstance(part, str) for part in source):
                raise RuntimeError(f'Stage39 notebook cell {index} source is invalid: {path.name}')
            source = ''.join(source)
        if not isinstance(source, str):
            raise RuntimeError(f'Stage39 notebook cell {index} source is invalid: {path.name}')
        cells.append({'cell_type': cell['cell_type'], 'source': source})

    canonical = json.dumps(
        {'nbformat': 4, 'cells': cells},
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def main():
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if manifest.get('schema') != 'n2-stage39-board-package-manifest-v1':
        raise RuntimeError('Stage39 board manifest identity mismatch')
    if manifest.get('package_revision') != PACKAGE_REVISION:
        raise RuntimeError(
            'Stage39 board package revision mismatch; upload the cold-reproduction 2026-10-12 r1 ZIP')

    for relative, expected in manifest['files'].items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError('Stage39 board immutable file verification failed: ' + relative)

    notebook_sources = manifest.get('notebook_sources')
    if not isinstance(notebook_sources, dict) or not notebook_sources:
        raise RuntimeError('Stage39 notebook source manifest is missing')
    for relative, expected in notebook_sources.items():
        path = ROOT / relative
        if not path.is_file() or notebook_source_sha(path) != expected:
            raise RuntimeError('Stage39 notebook source verification failed: ' + relative)

    artifact = json.loads((ROOT / 'sim/stage38_artifact_manifest.json').read_text(encoding='utf-8'))
    if artifact.get('frequency_mhz') != 95.0 or not artifact.get('timing_met') or not artifact.get('single_bitstream_four_scenes'):
        raise RuntimeError('95 MHz timing-qualified common bitstream proof missing')
    if sha(ROOT / 'sim/n2_stage38_raw.bit') != artifact['bit_sha256']:
        raise RuntimeError('bitstream hash mismatch')

    service = (ROOT / 'jupyter/stage39_board_service.py').read_text(encoding='utf-8')
    import_guard = (ROOT / 'jupyter/stage39_import_guard.py').read_text(encoding='utf-8')
    gate = (ROOT / 'jupyter/stage39_physical_gate.py').read_text(encoding='utf-8')
    if "SERVICE_BUILD_ID='stage39-hardware-exact-qat-four-scene-service-v1'" not in service:
        raise RuntimeError('Stage39 service identity missing')
    if ('prepare_stage39_imports(ROOT)' not in service or
            'spec_from_file_location("n2_stream"' not in import_guard or
            'REQUIRED_N2_STREAM_SYMBOLS' not in import_guard):
        raise RuntimeError('Stage39 deterministic protocol import guard is missing')
    if 'migrate_stage37_raw_evidence' in service or 'n2-stage39-all-test-identity-v1' not in service:
        raise RuntimeError('Stage39 full-test evidence is not isolated from older runtime weights')
    if 'n2-stage39-physical-evidence-export-v1' not in gate or 'export_evidence' not in gate:
        raise RuntimeError('Stage39 physical evidence exporter is missing')

    registry = json.loads((ROOT / 'scene_registry.json').read_text(encoding='utf-8'))
    if not isinstance(registry, dict):
        raise RuntimeError('Stage39 scene registry is invalid')
    bit_hashes = set()
    for scene in SCENES:
        spec = json.loads((ROOT / 'scenes' / scene / 'scene.json').read_text(encoding='utf-8'))
        hardware = spec['hardware']
        bit_hashes.add(hardware['bit_sha256'])
        model = ROOT / 'scenes' / scene / 'model/stage37_runtime_model.npz'
        contract = ROOT / 'scenes' / scene / 'model/stage39_physical_selftest.json'
        if sha(model) != hardware['runtime_model_sha256'] or not contract.is_file():
            raise RuntimeError(scene + ': runtime model or physical contract mismatch')
        contract_data = json.loads(contract.read_text(encoding='utf-8'))
        if contract_data['runtime_model_sha256'] != hardware['runtime_model_sha256'] or len(contract_data['raw_q15_sha256']) != 64:
            raise RuntimeError(scene + ': invalid physical exactness contract')
        if hardware.get('qat_algorithm_id') != 'stage39-hardware-exact-qat-ste-v1' or hardware.get('output_calibration') is not None:
            raise RuntimeError(scene + ': invalid QAT/no-calibration policy')
    if len(bit_hashes) != 1 or artifact['bit_sha256'] not in bit_hashes:
        raise RuntimeError('single-bitstream declaration mismatch')

    print('STAGE39 BOARD PACKAGE VERIFY PASS')
    print('immutable_files_checked =', len(manifest['files']))
    print('notebook_sources_checked =', len(notebook_sources))
    print('package_revision =', PACKAGE_REVISION)
    print('frequency_mhz = 95.0; scenes = 4')


if __name__ == '__main__':
    main()
