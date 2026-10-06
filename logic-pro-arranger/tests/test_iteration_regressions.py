"""Regression gates exercise normal iteration as well as rejected writes."""
import errno
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import logicsong as L
import check_midi as C
import analyze_audio as A
import vocal_tools as V
import sample_library as SL
from new_song import build
from tool_support import output_path
from music_understanding import tool_support as IO


def spec(title='test'):
    return dict(title=title, bpm=120, key='D# major', sections=[dict(name='A', bars=2, chords='C')],
                tracks=[dict(id='p', channel=1, program=0)])


def test_generated_edit_rerun_and_external_edit_protection(tmp_path):
    source = tmp_path/'song.py'
    code = build('draft','custom','','D# major',120,'').replace('    pass\n\n\n# 内嵌',
                '    song.track("lead").line(1,0,"C4:1/3 E4:1/3 G4:1/3", accent=0)\n\n\n# 内嵌')
    source.write_text(code)
    def run(): return subprocess.run([sys.executable,'-I',str(source)],capture_output=True,text=True)
    assert run().returncode == 0
    midi = tmp_path/'draft.mid'; before = midi.read_bytes()
    assert run().returncode == 0 and midi.read_bytes() == before
    source.write_text(code.replace('C4:1/3','D4:1/3'))
    assert run().returncode == 0 and midi.read_bytes() != before
    assert C.Midi(midi).tracks[1]['notes'][0][3] == 62
    midi.write_bytes(midi.read_bytes()+b'externally changed')
    external = midi.read_bytes(); result=run()
    assert result.returncode == 2 and midi.read_bytes() == external
    assert str(midi) in result.stderr and '.midi-' not in result.stderr and 'Traceback' not in result.stderr
    assert '改动过' in result.stderr and '--force' in result.stderr
    assert midi.stat().st_mode & 0o044


@pytest.mark.parametrize('key,expected',[('D# major',(-3,0)),('b minor',(2,1)),('Bb minor',(-5,1)),('Ab minor',(-7,1))])
def test_enharmonic_and_lowercase_key(key,expected,tmp_path):
    s=spec();s['key']=key
    assert L.key_signature(key)==expected
    assert C.Midi(L.Song(s).write(tmp_path)).keysigs[0][1:]==expected


def test_extensions_aliases_and_trailing_bar():
    assert L.chord_pcs('Bø7')==L.chord_pcs('Bm7b5')
    assert L.chord_pcs('C°7')==L.chord_pcs('Cdim7')
    assert {x%12 for x in L.voicing('C13',48,84,size=4)}=={0,4,10,9}
    assert {x%12 for x in L.voicing('C11',48,84,size=4)}=={0,4,10,5}
    s=spec();s['sections']=[dict(name='A',bars=8,chords='Am | F | C | G |')]
    song=L.Song(s)
    assert [song.chord(b) for b in range(1,9)]==['Am','F','C','G']*2


@pytest.mark.parametrize('method',[lambda t:t.line(1,0,'C4:1',vel=128),lambda t:t.grid(1,'x','C4',vel=128),lambda t:t.pad([1],48,72,vel=128),lambda t:t.groove(1,'x','kick',vel=128)])
def test_velocity_validation_all_paths(method):
    with pytest.raises(ValueError):method(L.Song(spec()).track('p'))


def test_moved_sections_jitter_and_negative_push_stay_equal(tmp_path):
    def make(title,intro,velocity=80):
        s=spec(title);s['sections']=[dict(name='intro',bars=intro,chords='C'),dict(name='locked',bars=2,chords='C'),dict(name='tail',bars=1,chords='C')]
        s['tracks'][0].update(jitter_ms=12,vel_jitter=9,push_ms=-20)
        song=L.Song(s);t=song.track('p')
        t.note(song.start('locked'),0,1,'C4',velocity);t.note(song.start('locked')+1,2,1,'E4',velocity)
        t.note(song.start('tail'),0,1,'G4')
        m=C.Midi(song.write(tmp_path));return m,C.analyze(m,s)
    old,ro=make('old',1);new,rn=make('new',2)
    table,locks,_=C.compare(new,old,rn,ro,dict(locked=['p@locked']))
    assert table['p','locked']=='=' and locks[0]['sev']=='OK'
    new,rn=make('changed',2,30)
    assert C.compare(new,old,rn,ro,dict(locked=['p@locked']))[1][0]['sev']=='ERROR'


def test_no_hardlinks_fallback_is_exclusive_and_readable(tmp_path,monkeypatch):
    monkeypatch.setattr(IO.os,'link',lambda *a:(_ for _ in ()).throw(OSError(errno.EOPNOTSUPP,'not supported')))
    out=tmp_path/'external-disk.mid'
    with output_path(out) as temp:temp.write_bytes(b'complete')
    assert out.read_bytes()==b'complete' and out.stat().st_mode & 0o044
    with pytest.raises(FileExistsError):
        with output_path(out) as temp:temp.write_bytes(b'bad')
    assert out.read_bytes()==b'complete'


def test_report_rerun_and_output_failure_codes(tmp_path):
    s=spec();song=L.Song(s);song.track('p').note(1,0,1,'C4');mid=song.write(tmp_path)
    out=tmp_path/'report.json'
    cmd=[sys.executable,str(SCRIPTS/'check_midi.py'),mid,'--json',str(out)]
    first=subprocess.run(cmd,capture_output=True);second=subprocess.run(cmd,capture_output=True)
    assert first.returncode==second.returncode and second.returncode in (0,1)
    out.write_text('external')
    assert subprocess.run(cmd,capture_output=True).returncode==2 and out.read_text()=='external'


def test_c2_info_and_syllables(tmp_path):
    rate=22050;t=np.arange(rate*2)/rate;y=.5*np.sin(2*np.pi*65.4064*t)
    _,_,_,_,active,clear=V.voicing(y,rate)
    assert clear.sum()/active.sum()>.9
    syllables=V.find_syllables(y[:,None],rate)
    assert syllables and abs(syllables[0]['midi']-36)<.15
    file=tmp_path/'c2.wav';sf.write(file,y,rate)
    result=subprocess.run([sys.executable,str(SCRIPTS/'vocal_tools.py'),'info',str(file)],capture_output=True,text=True)
    assert result.returncode==0 and 'MIDI 36.' in result.stdout and '清晰有声帧比例 0%' not in result.stdout


def test_timeline_full_bandwidth_matches_overall(tmp_path):
    sr=48000;t=np.arange(sr*3)/sr;mid=.25*np.sin(2*np.pi*1000*t);side=.125*np.sin(2*np.pi*16000*t)
    file=tmp_path/'wide.wav';sf.write(file,np.column_stack([mid+side,mid-side]),sr,subtype='FLOAT')
    report=A.analyze(str(file),use_aubio=False)
    assert abs(report['stereo']['side_mid_db']+6)<.1
    assert all(abs(row['side_mid_db']-report['stereo']['side_mid_db'])<.1 for row in report['timeline'])


def test_riff_vorbis_scan_and_prepare(tmp_path):
    root=tmp_path/'library';root.mkdir();wav=tmp_path/'input.wav';ogg=tmp_path/'audio.ogg'
    sf.write(wav,.3*np.sin(np.arange(22050)*.03),22050)
    subprocess.run([IO.find_tool('ffmpeg'),'-v','error','-i',str(wav),str(ogg)],check=True)
    payload=ogg.read_bytes();fmt=struct.pack('<HHIIHH',0x674f,1,22050,0,1,0)
    body=b'WAVE'+b'fmt '+struct.pack('<I',len(fmt))+fmt+b'data'+struct.pack('<I',len(payload))+payload+b'\0'*(len(payload)%2)
    disguised=root/'HH_Closed_01.wav';disguised.write_bytes(b'RIFF'+struct.pack('<I',len(body))+body)
    item=SL.inspect(disguised,root,IO.find_tool('ffprobe'))
    assert item['extension_mismatch'] and item['wav_format']==0x674f and item['role_hint']=='hat'
    assert 'metadata_error' not in item
    output=tmp_path/'prepared.wav';SL.prepare(dict(root=str(root),files=[item]),item['path'],output)
    assert sf.info(output).frames==22050


def test_unreadable_sample_is_recorded(tmp_path,monkeypatch):
    src=tmp_path/'kick.wav';src.write_bytes(b'x')
    original=Path.open
    def unreadable(path,*a,**kw):
        if path==src: raise PermissionError('fixture: read denied')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',unreadable)
    result=SL.scan(tmp_path,tmp_path.parent/(tmp_path.name+'.json'))
    assert len(result['files'])==1 and result['files'][0]['metadata_error']
    assert SL.summary(result)['metadata_errors']==1


def test_preview_single_bar_and_hooks_output(tmp_path,monkeypatch):
    import preview as P
    s=spec();s['sections'][0]['bars']=4
    song=L.Song(s);song.track('p').note(3,0,1,'C4');mid=song.write(tmp_path)
    rendered=[]
    monkeypatch.setattr(P,'render',lambda doc,out,**kw:(rendered.append(out),out)[1])
    monkeypatch.setattr(sys,'argv',['preview',mid,'--bars','3','--out',str(tmp_path/'one.mp3')])
    P.main();assert rendered[-1].endswith('one.mp3')
    s['hook_candidates']={'A':dict(line='C4:1 E4:1 G4:2'),'B':dict(line='D4:1 F4:1 A4:2')}
    script=tmp_path/'hooks.py';script.write_text('SPEC = '+repr(s))
    monkeypatch.setattr(sys,'argv',['preview','--hooks',str(script),'--out',str(tmp_path/'choice.mp3')])
    P.main();assert rendered[-2:]==[str(tmp_path/'choice-A.mp3'),str(tmp_path/'choice-B.mp3')]


@pytest.mark.parametrize('flag,value',[('--sections','broken'),('--sections','A:x'),('--sections','A:0'),('--sections','A:2:x')])
def test_new_song_bad_sections_is_usage_error(tmp_path,flag,value):
    result=subprocess.run([sys.executable,str(SCRIPTS/'new_song.py'),'--root',str(tmp_path),'--title','draft',
        '--key','C major','--bpm','120',flag,value],capture_output=True,text=True)
    assert result.returncode==2 and 'Traceback' not in result.stderr
    assert not list(tmp_path.iterdir())


def test_compare_preserves_empty_trailing_bars_without_new_spec(tmp_path):
    s=spec();s['sections'][0]['bars']=8;s['tracks'][0]['push_ms']=-20
    song=L.Song(s);song.track('p').note(3,0,1,'C4');m=C.Midi(song.write(tmp_path))
    table,locks,_=C.compare(m,m,C.analyze(m,s),C.analyze(m,None),dict(locked=['p@A']))
    assert table['p','A']=='=' and locks[0]['sev']=='OK'


def test_actual_legacy_marker_upgrade_keeps_executable_entry(tmp_path):
    from upgrade_song import upgrade
    code=build('legacy','custom','','C major',120,'').replace('    pass\n\n\n# 内嵌',
        '    song.track("lead").note(1,0,1,"C4")\n\n\n# 内嵌')
    code=code.replace('# 内嵌工具库，生成脚本可独立运行。\n# ==== LIB START ====','''# ======================================================================== 工具库（来自 logic-pro-arranger/scripts/logicsong.py，勿改）''')
    code=code.replace('# ==== LIB END ====','').replace("if __name__ == '__main__':",'if __name__ == "__main__":')
    path=tmp_path/'upgraded.py';path.write_text(upgrade(code))
    result=subprocess.run([sys.executable,'-I',str(path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert C.Midi(tmp_path/'legacy.mid').tracks[1]['notes'][0][3]==60


def test_generated_project_moves_without_losing_output_ownership(tmp_path):
    import shutil
    first=tmp_path/'before';first.mkdir();script=first/'song.py'
    script.write_text(build('draft','custom','','C major',120,'').replace('    pass\n\n\n# 内嵌',
                      '    song.track("lead").note(1,0,1,"C4")\n\n\n# 内嵌'))
    subprocess.run([sys.executable,'-I',str(script)],check=True,capture_output=True)
    producer=C.Midi(first/'draft.mid').producer
    assert producer and len(producer)==32
    shutil.move(first,tmp_path/'after')
    subprocess.run([sys.executable,'-I',str(tmp_path/'after/song.py')],check=True,capture_output=True)
    assert C.Midi(tmp_path/'after/draft.mid').producer==producer


def test_absolute_jitter_anchor_reproduces_pre_r2_humanization(monkeypatch):
    s=spec();s['tracks'][0].update(jitter_ms=8,vel_jitter=6)
    monkeypatch.setattr(L,'_JITTER_ANCHOR','absolute',raising=False)
    song=L.Song(s);song.track('p').note(2,1,1,'C4',80)
    t=song.tick(2,1);start,_,_,vel=song.track('p').notes[0]
    assert start==max(0,t+song.ms_to_ticks(L._h01(song.seed,'p',t,60,'t')*8,t))
    assert vel==max(1,min(127,int(round(80+L._h01(song.seed,'p',t,60,'v')*6))))


@pytest.mark.parametrize('version,anchor',[('0.2.0','absolute'),(None,'absolute'),('0.2.0-r2',None)])
def test_upgrade_keeps_jitter_anchor_of_source_version(version,anchor):
    from upgrade_song import upgrade
    lib=(SCRIPTS/'logicsong.py').read_text().split('# ==== LIB START ====')[1].split('# ==== LIB END ====')[0]
    code=build('old','custom','','C major',120,lib)
    code=code.replace(f'LIB_VERSION = "{L.LIB_VERSION}"',f'LIB_VERSION = "{version}"' if version else '')
    anchors=lambda src:[l for l in src.splitlines() if l.startswith('_JITTER_ANCHOR')]
    upgraded=upgrade(code)
    assert anchors(upgraded)==([f'_JITTER_ANCHOR = {anchor!r}'] if anchor else [])
    assert anchors(upgrade(upgraded))==anchors(upgraded)
    assert anchors(upgrade(upgraded,'section'))==[]


def strip_r2_metadata(data):
    """模拟 0.2.0-r2 之前写出的 MIDI：没有 LSM1/LSN1 元数据，也没有 CC11 初值。"""
    out,pos=bytearray(data[:14]),14
    while pos<len(data):
        ln=int.from_bytes(data[pos+4:pos+8],'big');events,_=C._parse_track(data[pos+8:pos+8+ln]);pos+=8+ln
        keep=[]
        for t,kind,a,b in events:
            if kind=='meta':
                if a==0x2F or a==0x7F and b[:5] in (b'LSM1:',b'LSN1:'):continue
                keep.append((t,0,L._meta(a,bytes(b))))
            elif not (kind==0xB0 and b[0]==11 and t==0):
                keep.append((t,0,bytes([kind|a,*b])))
        out+=L._chunk(keep)
    return bytes(out)


def test_compare_against_pre_r2_midi_has_no_false_changes(tmp_path):
    s=spec();s['sections']=[dict(name='A',bars=2,chords='C'),dict(name='B',bars=1,chords='G')]
    s['tracks'][0]['push_ms']=-10
    def write(cfg,folder):
        song=L.Song(cfg);t=song.track('p')
        t.note(1,0,1,'C4');t.note(3,0,8,'G4')      # 抢拍跨入前一段；尾音越过总长
        return C.Midi(song.write(tmp_path/folder))
    new=write(s,'new')
    old_path=tmp_path/'old.mid';old_path.write_bytes(strip_r2_metadata((tmp_path/'new'/'test.mid').read_bytes()))
    old=C.Midi(old_path);assert old.declared_bars is None
    table,locks,_=C.compare(new,old,C.analyze(new,s),C.analyze(old,None),dict(locked=['p@*']))
    assert set(table.values())=={'='} and all(r['sev']=='OK' for r in locks)
    louder=write(dict(s,tracks=[dict(s['tracks'][0],expression=100)]),'changed')
    table,_,_=C.compare(louder,new,C.analyze(louder,s),C.analyze(new,s),{})
    assert table['p','A']=='Δ(控制初值/program)'


def test_new_song_sections_error_names_the_field(tmp_path):
    result=subprocess.run([sys.executable,str(SCRIPTS/'new_song.py'),'--root',str(tmp_path),'--title','draft',
        '--key','C major','--bpm','120','--sections','主题:6:Fm'],capture_output=True,text=True)
    assert result.returncode==2 and '第 4 项' in result.stderr and 'invalid literal' not in result.stderr
