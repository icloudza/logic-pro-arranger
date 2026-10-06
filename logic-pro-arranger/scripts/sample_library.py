#!/usr/bin/env python3
"""本地采样索引：scan / search / summary / prepare。只读原库，缓存放项目外。

角色、BPM、音名标签来自命名，不等于听辨或音高测量。
SHA256 相同表示文件字节相同，不检测重编码/移调后的音频重复。
scan 必须指定 --root；其他命令只读取已建立的索引。
"""
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from tool_support import find_tool, output_path, write_text, publish, audio_input

AUDIO = {'.wav', '.mp3', '.flac', '.aif', '.aiff', '.m4a', '.ogg', '.caf'}
CACHE = Path.home()/'.cache/logic-pro-arranger/sample-library.json'
ROLE_PATTERNS = [
 ('kick', r'kick|底鼓'), ('snare-clap', r'snare|clap|军鼓|拍手'),
 ('hat', r'hi[- _]?hat|\bhh\b|shaker|踩镲'), ('cowbell', r'cowbell|\bcbl\b|牛铃'),
 ('bass', r'\bbass\b|808|reese|低音'), ('vocal', r'vocal|vox|acapella|chop|人声'),
 ('piano', r'piano|grand|钢琴'), ('music-box', r'music[_ ]?box|音乐盒'),
 ('bell', r'\bbell\b|钟|铃'), ('pad', r'\bpad\b|铺底'), ('lead', r'\blead\b|主音'),
 ('fx', r'\bfx\b|sweep|riser|crash|impact|音效'), ('percussion', r'perc|打击|鼓'),
]


def labels(relative):
    """先匹配文件名，再从最近父目录向上找，避免包名覆盖具体鼓件。"""
    p = Path(relative)
    role = 'unknown'
    for part in [p.stem] + list(reversed(p.parts[:-1])):
        hits = [name for name, pat in ROLE_PATTERNS if re.search(pat, part.replace("_", " "), re.I)]
        if hits:
            role = hits[0]
            break
    text = str(p)
    bpm = re.search(r'(?<!\d)(\d{2,3}(?:\.\d+)?)\s*bpm', text, re.I)
    note = re.search(r'(?:^|[_\s-])([A-G](?:#|b)?)(-?\d)?$', p.stem)
    kind = 'loop' if re.search(r'loop|循环',text,re.I) else 'one-shot' if re.search(r'one[ _-]?shot',text,re.I) else 'unknown'
    return {'role_hint':role, 'kind_hint':kind,
            'bpm_named':float(bpm.group(1)) if bpm else None,
            'note_named':''.join(x or '' for x in note.groups()) if note else None}


def wav_metadata(path):
    """读取 RIFF fmt/data/smpl；不解码，不猜测缺失的采样根音。"""
    out = {}
    with path.open('rb') as f:
        head = f.read(12)
        if len(head)!=12 or head[:4]!=b'RIFF' or head[8:]!=b'WAVE':
            out['container_hint'] = 'ogg' if head[:4] == b'OggS' else 'flac' if head[:4] == b'fLaC' else 'unknown'
            out['extension_mismatch'] = True
            return out
        data_size = 0
        size = path.stat().st_size
        while f.tell()+8<=size:
            chunk = f.read(8)
            tag, length = chunk[:4], struct.unpack('<I',chunk[4:])[0]
            start=f.tell()
            if start+length>size:
                break
            if tag==b'fmt ' and length>=16:
                codec,channels,sr,byte_rate,align,bits=struct.unpack('<HHIIHH',f.read(16))
                out.update(sample_rate=sr,channels=channels,bits_per_sample=bits,wav_format=codec)
                out['_byte_rate']=byte_rate
                if codec in (0x674f, 0x6750, 0x6751, 0x676f, 0x6770, 0x6771):
                    out.update(codec='vorbis', container_hint='riff-ogg', extension_mismatch=True)
            elif tag==b'data':
                data_size+=length
            elif tag==b'smpl' and length>=36:
                vals=struct.unpack('<9I',f.read(36))
                if vals[3]<=127:
                    out['root_midi_embedded']=vals[3]
                    out['pitch_fraction_embedded']=vals[4]
                out['loop_points_count']=vals[7]
            f.seek(start+length+(length%2))
        br=out.pop('_byte_rate',0)
        if br and out.get('wav_format') in (1,3,65534):
            out['duration_s']=round(data_size/br,6)
    return out


def inspect(path,root,ffprobe):
    try:
        return _inspect(path,root,ffprobe)
    except (OSError, ValueError) as exc:
        return dict(path=str(path.relative_to(root)), bytes=0, audio=path.suffix.lower() in AUDIO,
                    extension=path.suffix.lower(), **labels(str(path.relative_to(root))),
                    metadata_error=str(exc), sha256=None)


def _inspect(path,root,ffprobe):
    stat=path.stat()
    item={'path':str(path.relative_to(root)), 'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,
          'extension':path.suffix.lower(), 'audio':path.suffix.lower() in AUDIO}
    h=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''): h.update(part)
    item['sha256']=h.hexdigest()
    if item['audio']:
        item.update(labels(item['path']))
        try:
            if path.suffix.lower()=='.wav':item.update(wav_metadata(path))
            if 'duration_s' not in item:
                if not ffprobe:raise ValueError('缺少 ffprobe，未读取该格式时长')
                from tool_support import probe_audio
                d=probe_audio(path);a=d.get('streams',[{}])[0]
                item['duration_s']=round(float(d['format']['duration']),6)
                item['sample_rate']=int(a['sample_rate']);item['channels']=a['channels']
                item['codec']=a.get('codec_name')
                item['container']=d['format'].get('format_name')
                if path.suffix.lower()=='.wav':item['extension_mismatch']=item.get('extension_mismatch',False) or item['container']!='wav'
        except (ValueError,KeyError,IndexError,OSError,subprocess.SubprocessError) as e:
            item['metadata_error']=str(e)
    return item


def scan(root,index,workers=4):
    root=root.expanduser().resolve();index=index.expanduser().resolve()
    if not root.is_dir():raise ValueError(f'没有采样目录：{root}')
    if index==root or root in index.parents:
        raise ValueError('索引必须存放在采样库外，避免改动原库')
    paths=sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                 and not any(x.startswith('.') for x in p.relative_to(root).parts))
    ffprobe=find_tool('ffprobe')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        items=list(pool.map(lambda p:inspect(p,root,ffprobe),paths))
    data={'schema':1,'root':str(root),'scanned_at':datetime.now(timezone.utc).isoformat(),
          'evidence':'metadata and filenames; not auditory review','files':items}
    index.parent.mkdir(parents=True,exist_ok=True)
    write_text(index, json.dumps(data,ensure_ascii=False,separators=(',',':')), force=True)
    return data


def summary(data):
    items=data['files'];aud=[x for x in items if x['audio']]
    groups=defaultdict(list)
    for x in items:
        if x.get('sha256'): groups[x['sha256']].append(x)
    duplicates=[g for g in groups.values() if len(g)>1]
    packs=Counter(Path(x['path']).parts[0] for x in items)
    return {'root':data['root'],'files':len(items),'audio_files':len(aud),'bytes':sum(x['bytes'] for x in items),
        'packs':dict(packs),'extensions':dict(Counter(x['extension'] for x in items)),
        'roles_from_names':dict(Counter(x['role_hint'] for x in aud)),
        'duration_bins_s':{'under_2':sum(x.get('duration_s',1e9)<2 for x in aud),
            '2_to_10':sum(2<=x.get('duration_s',-1)<10 for x in aud),
            '10_or_more':sum(x.get('duration_s',0)>=10 for x in aud)},
        'extension_mismatches':[{'path':x['path'],'container':x.get('container',x.get('container_hint'))} for x in aud if x.get('extension_mismatch')],
        'metadata_errors':sum(bool(x.get('metadata_error')) for x in aud),
        'wav_extension_non_pcm_codecs':dict(Counter(x['codec'] for x in aud
            if x['extension']=='.wav' and x.get('codec') and not x['codec'].startswith('pcm_'))),
        'sample_rates':dict(Counter(x.get('sample_rate') for x in aud)),
        'embedded_root_notes':sum('root_midi_embedded' in x for x in aud),
        'embedded_root_histogram':dict(Counter(x['root_midi_embedded'] for x in aud
            if 'root_midi_embedded' in x)),
        'exact_duplicate_groups':len(duplicates),'extra_duplicate_files':sum(len(g)-1 for g in duplicates),
        'extra_duplicate_bytes':sum(sum(x['bytes'] for x in g[1:]) for g in duplicates)}


def prepare(data,relative,out):
    """选中一个素材，解码为标准 24-bit WAV；保留采样率/声道，不自动移调或归一化。"""
    root=Path(data['root']).resolve()
    item=next((x for x in data['files'] if x['path']==relative and x['audio']),None)
    if item is None:raise ValueError('请使用 search 返回的完整相对 path')
    source=(root/relative).resolve();out=out.expanduser().resolve()
    if root not in source.parents:raise ValueError('源文件不在采样库内')
    if out==root or root in out.parents:raise ValueError('输出必须在采样库外')
    if out.suffix.lower()!='.wav':raise ValueError('--out 必须是 .wav')
    manifest=out.with_suffix('.sample.json')
    if out.exists() or manifest.exists():raise ValueError('输出或素材记录已存在，请换文件名')
    h=hashlib.sha256()
    with source.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    if h.hexdigest()!=item['sha256']:raise ValueError('原文件已改变，请重新 scan')
    ffmpeg=find_tool('ffmpeg')
    if not ffmpeg:raise ValueError('prepare 需要 ffmpeg')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        staged=Path(tmp)/'sample.wav'
        hint=wav_metadata(source).get('container_hint') if source.suffix.lower()=='.wav' else None
        fmt=['-f',hint] if hint in ('ogg','flac') else []
        with audio_input(source) as normalized:
            result=subprocess.run([ffmpeg,'-nostdin','-v','error',*fmt,'-i',str(normalized),'-map','0:a:0',
                '-map_metadata','-1','-c:a','pcm_s24le',str(staged)],capture_output=True,text=True)
        if result.returncode:
            raise ValueError(f'解码失败（{source.name}，退出码 {result.returncode}）：{result.stderr.strip()[-1500:]}')
        # 同一文件系统内原子创建；已存在时失败，避免覆盖。
        publish(staged,out)
    record={'source':str(source),'source_sha256':item['sha256'],'output':str(out),
        'source_metadata':item,'processing':'decode to PCM 24-bit WAV; original sample rate and channels',
        'root_midi_verified':None,'trim_seconds':None,'target_bpm':None,'transpose_semitones':0}
    write_text(manifest,json.dumps(record,ensure_ascii=False,indent=2))
    return {'wav':str(out),'sample_record':str(manifest)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index',type=Path,default=CACHE)
    sub=p.add_subparsers(dest='command',required=True)
    sc=sub.add_parser('scan');sc.add_argument('--root',type=Path,required=True)
    sc.add_argument('--workers',type=int,choices=range(1,17),default=4)
    sub.add_parser('summary')
    se=sub.add_parser('search');se.add_argument('query',nargs='?',default='')
    se.add_argument('--role',choices=[x[0] for x in ROLE_PATTERNS]+['unknown'])
    se.add_argument('--limit',type=int,default=12);se.add_argument('--max-seconds',type=float)
    se.add_argument('--include-duplicates',action='store_true')
    pr=sub.add_parser('prepare');pr.add_argument('path')
    pr.add_argument('--out',type=Path,required=True)
    for parser in sub.choices.values():
        parser.add_argument('--index', type=Path, default=argparse.SUPPRESS, help='索引 JSON，可放在子命令前或后')
    p.epilog = '依赖：Python 标准库；压缩格式读取和 prepare 需要 ffmpeg/ffprobe。'
    a=p.parse_args()
    try:
        if a.command=='scan':data=scan(a.root,a.index,a.workers)
        else:
            with a.index.expanduser().open(encoding='utf-8') as f:data=json.load(f)
        if a.command in ('scan','summary'):print(json.dumps(summary(data),ensure_ascii=False,indent=2))
        elif a.command=='prepare':print(json.dumps(prepare(data,a.path,a.out),ensure_ascii=False,indent=2))
        else:
            count=0;seen=set()
            for x in data['files']:
                if not x['audio'] or (a.role and x['role_hint']!=a.role):continue
                if a.query.casefold() not in x['path'].casefold():continue
                if a.max_seconds is not None and x.get('duration_s',float('inf'))>a.max_seconds:continue
                if not a.include_duplicates and x['sha256'] in seen:continue
                if count>=max(0,a.limit):break
                out=dict(x,absolute_path=str(Path(data['root'])/x['path']))
                print(json.dumps(out,ensure_ascii=False));count+=1;seen.add(x['sha256'])
    except (ValueError,OSError,subprocess.SubprocessError) as e:p.exit(1,f'错误：{e}\n')


if __name__=='__main__':main()
