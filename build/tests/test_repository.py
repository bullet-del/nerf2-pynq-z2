"""Layout and numerical-contract integration checks; no hardware required."""
import importlib.util
import json
import unittest
import tempfile
from unittest import mock
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
C=load('repository_checks',ROOT/'build/tools/check_repository.py')

class RepositoryContract(unittest.TestCase):
    def test_actual_repository(self):
        self.assertEqual(C.check()['maps'],1725)

    def test_lowercase_rule_detects_previous_failure(self):
        self.assertIsNotNone(C.path_error('01_Vivado/rtl/core.v'))
        self.assertIsNotNone(C.path_error('data/scenes/Office1/result.json'))
        self.assertIsNone(C.path_error('src/rtl/core.v'))
        self.assertIsNone(C.path_error('skill/example/SKILL.md'))

    def test_model_and_scene_contract_stays_fixed(self):
        q=json.loads((ROOT/'build/config/reproduction_config.json').read_text(encoding='utf-8'))
        self.assertEqual(q['scenes']['s23']['factorization_steps'],0)
        for name in ['Meetingroom','Office1','Office2']:self.assertEqual(q['scenes'][name]['factorization_steps'],3000)
        self.assertEqual(q['acceptance']['weight_capacity_words'],3072)

    def test_local_config_and_generated_outputs_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            for name in ['README.md','build/config/reproduction_config.local.json','work/output.json','external/input.txt','build/output/new.bit']:
                p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('local fixture',encoding='utf-8')
            with mock.patch.object(C,'ROOT',base):
                names={p.relative_to(base).as_posix() for p in C.source_files()}
            self.assertEqual(names,{'README.md'})

if __name__=='__main__':unittest.main()
