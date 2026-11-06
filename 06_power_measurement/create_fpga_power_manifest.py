"""Create twelve editable alignment templates and a portable FPGA run list."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCENES = ('s23','Meetingroom','Office1','Office2')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); root=a.capture_root.resolve(); output=a.output.resolve()
    if output.exists(): raise FileExistsError(output)
    entries=[]
    for scene in SCENES:
        for repeat in range(1,4):
            stem=scene.lower()+'_r'+str(repeat)
            paths={key:root/(stem+suffix) for key,suffix in [('run','.json'),('cfn','.cfn'),('video','.mp4'),('alignment','_alignment.json')]}
            for key in ('run','cfn','video'):
                if not paths[key].is_file(): raise FileNotFoundError(paths[key])
            if not paths['alignment'].exists():
                subprocess.run([sys.executable,str(Path(__file__).with_name('prepare_power_alignment.py')),
                    '--run',str(paths['run']),'--cfn',str(paths['cfn']),'--video',str(paths['video']),
                    '--output',str(paths['alignment'])],check=True)
            entries.append({'scene':scene,'repeat':'R'+str(repeat),
                **{k:Path(os.path.relpath(v,output.parent)).as_posix() for k,v in paths.items() if k!='video'}})
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'schema':'stage39-fpga-power-captures-v1','runs':entries},indent=2)+'\n',encoding='utf-8')
    print('Fill the twelve alignment JSON files, then run collect_fpga_power.py with '+str(output))


if __name__=='__main__': main()
