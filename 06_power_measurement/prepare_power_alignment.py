"""Create a reviewed-video alignment template without guessing phase times."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from audit_board_power_capture import file_sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'cfn', 'video', 'output'): p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise FileExistsError('Keep the existing alignment: '+str(a.output))
    for f in (a.run,a.cfn,a.video):
        if not f.is_file(): raise FileNotFoundError(f)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    value = {'schema': 'stage39-screenrecord-time-alignment-v1', 'reviewed': False,
        'run_sha256': file_sha(a.run), 'cfn_sha256': file_sha(a.cfn),
        'video_file': Path(os.path.relpath(a.video.resolve(),a.output.parent.resolve())).as_posix(),
        'video_sha256': file_sha(a.video), 'video_fps': None, 'video_duration_s': None,
        'recorder_active_first_video_s': None, 'recorder_stopped_first_video_s': None,
        'recorder_stop_observed': True,
        'phase_first_visible_video_s': {key: None for key in ('idle_start','idle_end','active_start','active_end')},
        'boundary_shift_sensitivity_s': 0.2,
        'instructions': 'Fill seconds from the unedited video (not from power changes); set reviewed true only after checking all events.'}
    a.output.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(a.output)


if __name__ == '__main__': main()
