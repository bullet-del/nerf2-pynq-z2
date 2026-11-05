"""Download published input/deployment packages; validate size and ZIP structure."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ALIASES={'inputs':'nerf2_inputs.zip','board':'board_package.zip','pc':'pc_app_package.zip',
         'results':'test_results.zip','power':'power_logs.zip','video':'demonstration.mp4'}

def validate_file(path,item):
    if not path.is_file() or path.stat().st_size!=item['bytes']:raise RuntimeError('Incomplete download: '+str(path))
    if path.suffix=='.zip':
        with zipfile.ZipFile(path) as z:
            if z.testzip():raise RuntimeError('ZIP data error: '+str(path))

def extract_new(archive,destination):
    destination=destination.resolve();destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            target=(destination/item.filename).resolve()
            if target!=destination and destination not in target.parents:raise RuntimeError('Unsafe archive path')
            if target.exists() and not item.is_dir():raise FileExistsError(str(target))
        z.extractall(destination)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset',choices=['all',*ALIASES],default='all')
    parser.add_argument('--destination',type=Path,default=ROOT/'external/downloads')
    parser.add_argument('--extract-inputs',action='store_true')
    args=parser.parse_args()
    items=json.loads((ROOT/'data/downloads.json').read_text(encoding='utf-8'))['assets']
    args.destination.mkdir(parents=True,exist_ok=True)
    for item in items:
        if args.asset!='all' and item['name']!=ALIASES[args.asset]:continue
        path=args.destination/item['name']
        if not path.exists():
            partial=path.with_suffix(path.suffix+'.partial')
            print('Downloading '+item['name'],flush=True)
            request=urllib.request.Request(item['url'],headers={'User-Agent':'stage39-artifact-downloader'})
            with urllib.request.urlopen(request,timeout=60) as response,partial.open('wb') as stream:
                shutil.copyfileobj(response,stream,length=1024*1024)
            if partial.stat().st_size!=item['bytes']:raise RuntimeError('Incomplete download; run again')
            os.replace(partial,path)
        validate_file(path,item)
        print('READY '+str(path),flush=True)
    if args.extract_inputs:
        item=next(x for x in items if x['name']==ALIASES['inputs'])
        validate_file(args.destination/item['name'],item)
        extract_new(args.destination/item['name'],ROOT/'external')
        print('Inputs extracted to external/nerf2_inputs')

if __name__=='__main__':main()
