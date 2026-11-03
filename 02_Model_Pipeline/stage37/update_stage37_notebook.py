#!/usr/bin/env python3
"""Make the Stage37 Notebook launch a detached supervised board service."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "outputs/N2-NeRF2-Stage37-board/Stage37_Universal_Four_Scenes.ipynb"


def main() -> None:
    if NOTEBOOK.is_file():
        value = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    else:
        value = {
            "cells": [
                {"cell_type": "markdown", "metadata": {}, "source": []},
                {"cell_type": "code", "execution_count": None, "metadata": {},
                 "outputs": [], "source": [
                    "from pathlib import Path\n",
                    "import os, sys, importlib\n",
                    "HOME = Path('/home/xilinx/jupyter_notebooks')\n",
                    "CANDIDATES = [Path.cwd(), HOME/'N2-NeRF2-Stage37']\n",
                    "ROOT = next((x.resolve() for x in CANDIDATES if (x/'jupyter/stage37_board_service.py').is_file()), None)\n",
                    "assert ROOT is not None, '找不到新版 Stage37 板端目录，请重新解压 ZIP'\n",
                    "os.chdir(str(ROOT))\n",
                    "for rel in ('jupyter','software/stage5','software/stage8','software/stage10','software/stage24'):\n",
                    "    path = str((ROOT/rel).resolve())\n",
                    "    if path not in sys.path: sys.path.insert(0, path)\n",
                    "importlib.invalidate_caches()\n",
                    "required = [ROOT/'sim/n2_stage37_universal.bit', ROOT/'sim/n2_stage37_universal.hwh',\n",
                    "            ROOT/'sim/stage37_artifact_manifest.json', ROOT/'scene_registry.json']\n",
                    "missing = [str(x) for x in required if not x.is_file()]\n",
                    "assert not missing, '板端包不完整：' + '; '.join(missing)\n",
                    "print('ROOT =', ROOT)\n",
                    "print('Stage37 files PASS')\n",
                 ]},
                {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": []},
                {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": []},
            ],
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "version": "3.10"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    code_cells = [cell for cell in value.get("cells", []) if cell.get("cell_type") == "code"]
    if len(code_cells) != 3:
        raise RuntimeError("Stage37 Notebook must contain exactly three code cells")
    markdown_cells = [cell for cell in value.get("cells", []) if cell.get("cell_type") == "markdown"]
    if markdown_cells:
        markdown_cells[0]["source"] = [
            "# N2-NeRF² Stage37：单一位流四场景后台服务\n",
            "\n",
            "首次换用本修复版时先重启 PYNQ 板一次，再按顺序运行下面 3 个代码单元格。"
            "第 2 格启动独立后台服务，第 3 格核对状态；三个单元格都会运行结束，"
            "不需要保持 Notebook 页面打开。演示期间不要运行其他控制 FPGA 的 Notebook。\n",
        ]
    code_cells[1]["source"] = [
        "# 启动独立于 Jupyter 内核的受监督板端服务。此单元格运行完成后会立即返回。\n",
        "from stage37_service_launcher import start\n",
        "service = start(host='0.0.0.0', port=8311)\n",
        "print('Stage37 service =', service['url'])\n",
        "print('service build =', service['health']['service_build_id'])\n",
        "print('单元格已经结束；现在可以关闭本 Notebook 页面并打开电脑 App。')\n",
        "service\n",
    ]
    code_cells[2]["source"] = [
        "# 第三个单元格只核对后台服务状态，也会立即运行结束。\n",
        "from stage37_service_launcher import status\n",
        "service_status = status(port=8311)\n",
        "assert service_status['ok'], service_status\n",
        "print('后台服务运行正常：http://192.168.2.99:8311')\n",
        "print('不需要保持任何 Notebook 单元格运行。')\n",
        "service_status\n",
    ]
    NOTEBOOK.write_text(
        json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
