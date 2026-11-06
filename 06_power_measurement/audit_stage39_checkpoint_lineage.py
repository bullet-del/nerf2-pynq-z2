"""Audit original checkpoint -> structural adaptation -> QAT -> runtime model."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import torch

ROOT = Path(__file__).resolve().parents[1]
SCENES = ('s23', 'Meetingroom', 'Office1', 'Office2')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default = ROOT/'00_config/reproduction_config.local.json'
    parser.add_argument('--config', type=Path, default=default if default.is_file() else ROOT/'00_config/reproduction_config.json')
    parser.add_argument('--board-root', type=Path, default=ROOT/'outputs/N2-NeRF2-Stage39-board')
    parser.add_argument('--output-root', type=Path, default=ROOT/'work/full/reports/lineage')
    args = parser.parse_args(); config = load(args.config)
    inputs = Path(config['input_root'])
    if not inputs.is_absolute(): inputs = ROOT/inputs
    reports = []
    for scene in SCENES:
        spec = config['scenes'][scene]; root = args.board_root/'scenes'/scene
        original = inputs/spec['checkpoint']
        adapted = root/'pc_app/model/checkpoint.tar'
        adaptation = torch.load(adapted, map_location='cpu')['stage37']
        qat = load(root/'source/stage39_qat/checkpoint_qat.json')
        runtime_path = root/'source/stage39_qat/package_manifest.json'
        if not runtime_path.is_file(): runtime_path = runtime_path.with_name('runtime_package_manifest.json')
        runtime = load(runtime_path)
        quant = load(root/'model/quant_manifest.json')
        evaluation = load(root/'source/stage39_qat/full_evaluation.json')
        actual_source = sha(original); adapted_hash = sha(adapted)
        checks = {
            'original_is_selected_checkpoint': actual_source == spec['checkpoint_sha256'],
            'original_is_last_in_its_experiment': original.name == sorted(original.parent.glob('*.tar'))[-1].name,
            'adaptation_from_original': adaptation['source_checkpoint_sha256'] == actual_source,
            'adaptation_steps_unchanged': int(adaptation['steps']) == int(spec['factorization_steps']),
            'qat_from_adaptation': qat['source_checkpoint_sha256'] == adapted_hash,
            'quant_from_adaptation': quant['checkpoint_sha256'] == adapted_hash,
            'quant_from_qat': quant['qat_checkpoint_sha256'] == qat['checkpoint_sha256'],
            'runtime_from_qat': runtime['checkpoint_sha256'] == qat['checkpoint_sha256'],
            'runtime_bytes_match': sha(root/'model/stage37_runtime_model.npz') == runtime['runtime_model_sha256'],
            'runtime_fits_capacity': runtime['fits_unchanged_bitstream'] is True,
            'runtime_selection_is_qat': runtime['selection'] in {'qat', 'hardware-exact-qat'},
            'complete_test_split': int(evaluation['maps']) == int(spec['test_count']),
            'no_test_data_in_qat': qat.get('test_artifacts_opened') == [],
            'train_split_matches': qat['train_index_sha256'] == sha(inputs/spec['dataset']/'train_index.txt'),
        }
        reports.append({'scene': scene, 'status': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks})
    report = {'schema': 'stage39-checkpoint-lineage-audit-v2',
              'status': 'PASS' if all(r['status']=='PASS' for r in reports) else 'FAIL', 'scenes': reports}
    args.output_root.mkdir(parents=True, exist_ok=True)
    out = args.output_root/'checkpoint_lineage.json'
    out.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if report['status'] != 'PASS': raise SystemExit(1)


if __name__ == '__main__': main()
