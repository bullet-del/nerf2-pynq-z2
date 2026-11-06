"""Read the installed board environment and HDMI dependency; never load a bitstream."""
from __future__ import annotations
import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('evidence/stage39/board_environment.json'))
    a=p.parse_args(); import pynq,numpy,PIL
    base=importlib.util.find_spec('pynq.overlays.base')
    pairs=[]
    if base and base.origin:
        for bit in Path(base.origin).parent.glob('*.bit'):
            pairs.append({'bit':str(bit),'hwh':str(bit.with_suffix('.hwh')),
                          'paired_hwh_exists':bit.with_suffix('.hwh').is_file()})
    value={'schema':'stage39-board-environment-v1','python':sys.version,'platform':platform.platform(),
        'pynq':pynq.__version__,'numpy':numpy.__version__,'pillow':PIL.__version__,
        'base_module':base.origin if base else None,'base_overlay_pairs':pairs,
        'os_release':Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else None,
        'pynq_release':Path('/etc/pynq-release').read_text() if Path('/etc/pynq-release').exists() else None,
        'bitstream_loaded_by_this_command':False}
    if not any(x['paired_hwh_exists'] for x in pairs): raise RuntimeError('Installed base HDMI BIT/HWH pair is missing')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(value,indent=2))


if __name__=='__main__':main()
