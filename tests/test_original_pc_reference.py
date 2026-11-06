"""The App must reject cached, partial or mixed-checkpoint timing records."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'03_package_builder/original_pc_timing_contract.py'
spec=importlib.util.spec_from_file_location('original_pc_contract_test',SOURCE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class OriginalReference(unittest.TestCase):
    def check(self,changes,ok=False):
        value={'schema':'stage39-original-nerf2-pc-timing-v1','status':'PASS','full_test_set':True,
            'original_nerf2_forward':True,'fixed_rx_cache_used':False,'checkpoint_sha256':'original',
            'spectrum_ids':['1','2'],'sample_count':2,'mean_end_to_end_seconds_per_spectrum':1.5,
            'per_map':[{'spectrum_id':'1','test_ordinal':0,'duration_s':1.0},
                       {'spectrum_id':'2','test_ordinal':1,'duration_s':2.0}]}
        value.update(changes)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'timing.json';p.write_text(json.dumps(value))
            if ok: module.require_original_pc_timing(p,'original',['1','2'])
            else:
                with self.assertRaises(RuntimeError):module.require_original_pc_timing(p,'original',['1','2'])
    def test_complete_original_graph(self):self.check({},True)
    def test_reject_fixed_rx_cache(self):self.check({'fixed_rx_cache_used':True})
    def test_reject_other_checkpoint(self):self.check({'checkpoint_sha256':'adapted'})
    def test_reject_partial_run(self):self.check({'full_test_set':False})
    def test_reject_missing_per_map_records(self):self.check({'per_map':[]})
    def test_reject_mixed_test_order(self):self.check({'spectrum_ids':['2','1']})
    def test_reject_invalid_time(self):self.check({'mean_end_to_end_seconds_per_spectrum':0.0})


if __name__=='__main__':unittest.main()
