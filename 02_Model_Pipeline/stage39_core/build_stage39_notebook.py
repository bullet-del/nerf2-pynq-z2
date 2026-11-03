#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/N2-NeRF2-Stage39-board/Stage39_QAT_Four_Scenes.ipynb'

def code(text):
    return {'cell_type':'code','execution_count':None,'metadata':{},'outputs':[],
            'source':[line+'\n' for line in text.strip().splitlines()]}

cells=[
 code('''# Stage39：验证完整板端包并建立绝对路径；Notebook 仅校验单元格源内容，允许 Jupyter 写入执行状态。
from pathlib import Path
import os, sys, subprocess
ROOT=Path('/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board')
assert (ROOT/'jupyter/stage39_service_launcher.py').is_file(), '找不到完整 Stage39 board 文件夹，请重新上传新版 ZIP'
os.chdir(str(ROOT))
JUPYTER=str((ROOT/'jupyter').resolve())
if JUPYTER not in sys.path: sys.path.insert(0,JUPYTER)
for name in ('stage39_import_guard','stage39_service_launcher','stage39_physical_gate'):
    sys.modules.pop(name,None)
from stage39_import_guard import prepare_stage39_imports
protocol=prepare_stage39_imports(ROOT)
assert protocol.P==32 and Path(protocol.__file__).resolve()==(ROOT/'software/stage5/n2_stream.py').resolve()
subprocess.run([sys.executable,'software/stage39/verify_stage39_board_package.py'],check=True)
print('Stage39 root =',ROOT)'''),
 code('''# 启动独立于 Jupyter 内核的受监督板端服务；本单元格会返回，无须保持运行。
from stage39_service_launcher import start
service=start(host='0.0.0.0',port=8311,wait_seconds=120)
print('Stage39 service =',service['url'])
print('service build =',service['health']['service_build_id'])
assert service['health']['service_build_id']=='stage39-hardware-exact-qat-four-scene-service-v1' '''),
 code('''# Stage39.2 物理门禁：四场景各运行一幅完整 90×360 图，共核对 129,600 个原始 Q15 像素。
from stage39_physical_gate import run_selftests
physical_exact=run_selftests()
assert physical_exact['status']=='PASS', physical_exact
physical_exact'''),
 code('''# Stage39.3 正式时延与稳定性门禁：四场景各 50 次，共 200 幅完整硬件图。
# 正式录制/最终报告前运行；日常 App 演示不必重复执行。
from stage39_physical_gate import run_benchmarks
physical_benchmark=run_benchmarks(repeats=50)
assert physical_benchmark['status']=='PASS', physical_benchmark
physical_benchmark'''),
 code('''# 导出真实板卡证据。完成四场景总体精度后可再运行一次，以同时收录完整测试摘要。
from stage39_physical_gate import export_evidence
physical_export=export_evidence()
print('请下载：',physical_export['archive'])
physical_export'''),
 code('''# 可选：查看服务状态。App 使用期间服务独立运行，Notebook 内核可以关闭或重启。
from stage39_service_launcher import status
status()''')]
nb={'cells':cells,'metadata':{'kernelspec':{'display_name':'Python 3 (PYNQ)','language':'python','name':'python3'},'language_info':{'name':'python','version':'3.10'}},'nbformat':4,'nbformat_minor':5}
OUT.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
print(OUT)
if __name__=='__main__':pass
