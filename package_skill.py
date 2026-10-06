#!/usr/bin/env python3
"""生成 Claude / Codex 通用插件包（默认）或独立 skill 包；仅包含可移植源码。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'logic-pro-arranger/scripts'))
from tool_support import output_path

MANIFESTS=('.claude-plugin/plugin.json','.codex-plugin/plugin.json')


def package(destination, archive_format='plugin'):
    if archive_format not in ('plugin', 'skill'):
        raise ValueError('打包格式必须为 plugin 或 skill')
    source=ROOT/'logic-pro-arranger'
    prefix=Path('skills/logic-pro-arranger') if archive_format == 'plugin' else Path('logic-pro-arranger')
    manifests={}
    if archive_format == 'plugin':
        for path in MANIFESTS:
            manifest=json.loads((ROOT/path).read_text(encoding='utf-8'))
            # 源码仓库直接引用 skill；两个宿主的分发包共享标准 skills/ 布局。
            manifest['skills']='./skills/'
            manifests[path]=manifest
    excluded={'local','.venv','build','__pycache__','.pytest_cache','.git','.DS_Store'}
    with output_path(destination) as tmp, zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as archive:
        for path,manifest in manifests.items():
            archive.writestr(path, json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        if archive_format == 'plugin':
            archive.write(ROOT/'.agents/plugins/marketplace.json','.agents/plugins/marketplace.json')
        for folder,dirs,files in os.walk(source,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in excluded and not d.endswith('.egg-info') and not (Path(folder)/d).is_symlink())
            for file in sorted(files):
                p=Path(folder)/file
                if file in excluded or p.is_symlink() or p.suffix in ('.pyc','.pyo'):continue
                archive.write(p,prefix/p.relative_to(source))
    return destination


def release(destination, tag):
    """验证标签后生成两个宿主共用的插件、独立 skill、校验和与发布说明。"""
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?',tag):
        raise ValueError('发布标签需为 v主版本.次版本.修订号，可带预发布后缀')
    manifests=[json.loads((ROOT/path).read_text(encoding='utf-8')) for path in MANIFESTS]
    if any(m['name'] != 'logic-pro-arranger' or 'v'+m['version'] != tag for m in manifests):
        raise ValueError('发布标签必须与 Claude、Codex 清单的版本一致')
    version=tag[1:]
    changelog=(ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
    section=re.search(r'^## '+re.escape(version)+r' — [^\n]+\n(.*?)(?=^## |\Z)',changelog,re.M|re.S)
    if not section or not section[1].strip():
        raise ValueError('CHANGELOG.md 缺少该版本的发布记录')
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    stem='logic-pro-arranger-'+version
    plugin=package(destination/(stem+'.plugin'))
    # .plugin 与 .zip 是同一份插件内容；独立 skill 的文件名明确区分。
    zipped=destination/(stem+'.zip')
    shutil.copyfile(plugin,zipped)
    skill=package(destination/f'logic-pro-arranger-skill-{version}.zip','skill')
    checksums=''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in (plugin,zipped,skill))
    (destination/'SHA256SUMS').write_text(checksums,encoding='utf-8')
    notes=section[1].strip()+'\n\n'+(
        f'- Claude Desktop：上传 `{stem}.plugin`（或同内容的 `.zip`）。\n'
        f'- Codex 插件：解压 `{stem}.zip` 后添加为本地插件市场，或添加本仓库的 Git 插件市场。\n'
        f'- 独立 skill：使用 `logic-pro-arranger-skill-{version}.zip`。\n'
        '- 音频依赖需在执行环境安装；安装步骤见仓库 README。\n'
    )
    (destination/'RELEASE_NOTES.md').write_text(notes,encoding='utf-8')
    return destination


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--format',choices=('plugin','skill'),default='plugin',help='plugin：Claude / Codex 插件；skill：独立技能目录')
    ap.add_argument('--release',metavar='TAG',help='构建版本发布全套文件，例如 v0.2.1；--out 此时为新目录')
    ap.add_argument('--out',type=Path,required=True,help='输出文件（--release 时为新目录）；已有文件不覆盖')
    args=ap.parse_args()
    if args.release and args.format != 'plugin':
        ap.error('--release 自动生成全部格式，无需 --format skill')
    try:print(release(args.out,args.release) if args.release else package(args.out,args.format))
    except (ValueError,OSError) as exc:ap.exit(1,f'错误：{exc}\n')
