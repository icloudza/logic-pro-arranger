#!/usr/bin/env python3
"""Build a source-only, portable skill archive; local/runtime data are excluded."""
import argparse
import os
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'logic-pro-arranger/scripts'))
from tool_support import output_path


def package(destination):
    source=ROOT/'logic-pro-arranger'
    excluded={'local','.venv','build','__pycache__','.pytest_cache','.git','.DS_Store'}
    with output_path(destination) as tmp, zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as archive:
        for folder,dirs,files in os.walk(source,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in excluded and not d.endswith('.egg-info') and not (Path(folder)/d).is_symlink())
            for file in sorted(files):
                p=Path(folder)/file
                if file in excluded or p.is_symlink() or p.suffix in ('.pyc','.pyo'):continue
                archive.write(p,p.relative_to(ROOT))
    return destination


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    try:print(package(args.out))
    except (ValueError,OSError) as exc:ap.exit(1,f'错误：{exc}\n')
