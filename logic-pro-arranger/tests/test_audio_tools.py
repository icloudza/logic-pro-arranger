import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import analyze_audio as A
import vocal_tools as V
import sample_library as S


@pytest.mark.parametrize('script',['analyze_audio','vocal_tools','export_mp3','preview','sample_library','music'])
def test_help_without_site_packages(script):
    r=subprocess.run([sys.executable,'-S',str(SCRIPTS/(script+'.py')),'--help'],capture_output=True,text=True)
    assert r.returncode == 0, r.stderr
    if script!='music': assert '依赖' in r.stdout


def test_audio_outputs_never_clobber(tmp_path):
    source=tmp_path/'master.wav';A.write_wav(source,np.zeros(22050),22050)
    original=source.read_bytes()
    with pytest.raises(FileExistsError):A.write_wav(source,np.ones(22050),22050)
    alias=tmp_path/'MASTER.WAV'
    if not alias.exists():alias.hardlink_to(source)
    r=subprocess.run([sys.executable,str(SCRIPTS/'export_mp3.py'),str(source),'--out',str(alias),'--force'],capture_output=True,text=True)
    assert r.returncode and source.read_bytes()==original
    report=tmp_path/'report.json';report.write_text('sentinel')
    for argv in ([str(SCRIPTS/'analyze_audio.py'),str(source),'--json',str(report),'--no-aubio'],
                 [str(SCRIPTS/'vocal_tools.py'),'syllables',str(source),'--json',str(report)],
                 [str(SCRIPTS/'vocal_tools.py'),'align','--src',str(source),'--out',str(report),'--src-bpm','120','--downbeat','0','--bpm','120','--at-bar','1']):
        r=subprocess.run([sys.executable,*argv],capture_output=True,text=True)
        assert r.returncode and report.read_text()=='sentinel'


def test_wav16_follows_mp3_out(tmp_path):
    source=tmp_path/'master.wav';A.write_wav(source,np.zeros(22050),22050)
    out=tmp_path/'导出'/'作品.mp3';out.parent.mkdir()
    r=subprocess.run([sys.executable,str(SCRIPTS/'export_mp3.py'),str(source),'--out',str(out),'--wav16'],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    assert (tmp_path/'导出'/'作品_44k16.wav').is_file() and not (tmp_path/'master_44k16.wav').exists()


@pytest.mark.parametrize('offset',[.008,.137,.221])
def test_grid_offset(offset):
    sr=A.SR;period=60/130;y=np.zeros(sr*6,dtype=np.float32)
    for t in np.arange(offset,6,period):
        i=round(t*sr);n=min(round(.1*sr),len(y)-i);x=np.arange(n)/sr
        y[i:i+n]=np.sin(2*np.pi*90*x)*np.exp(-x*45)
    phase,_=A.beat_phase(A.onset_env(A.stft_mag(y,2048,A.HOP),sr),period*A.FR)
    error=(phase-offset+period/2)%period-period/2
    assert abs(error)<.012


def test_yin_low_c2():
    sr=22050;t=np.arange(sr)/sr
    f0,ap,_,_=V.yin(.5*np.sin(2*np.pi*65.4064*t),sr)
    assert abs(np.median(f0[ap<.1])-65.4064)<.3
    assert V.parse_note('C♯4')==61
    assert set(V.parse_chord('C6/9'))=={0,2,4,7,9}


def test_disguised_ogg(tmp_path):
    source=tmp_path/'library';source.mkdir()
    raw=tmp_path/'raw.wav';A.write_wav(raw,np.sin(np.arange(22050)*.1)*.1,22050)
    ogg=source/'fake.wav'
    import soundfile as sf
    sf.write(ogg,np.sin(np.arange(22050)*.1)*.1,22050,format='OGG',subtype='VORBIS')
    item=S.inspect(ogg,source,A.find_tool('ffprobe'))
    assert item['extension_mismatch'] and item['codec']=='vorbis' and 'metadata_error' not in item
    result=S.prepare({'root':str(source),'files':[item]},'fake.wav',tmp_path/'out.wav')
    assert Path(result['wav']).read_bytes()[:4]==b'RIFF'
    index=tmp_path/'index.json';index.write_text(json.dumps({'root':str(source),'files':[item]}))
    for args in (['--index',str(index),'search','fake'],['search','fake','--index',str(index)]):
        r=subprocess.run([sys.executable,str(SCRIPTS/'sample_library.py'),*args],capture_output=True,text=True)
        assert r.returncode==0 and 'fake.wav' in r.stdout


def test_chops_rejects_poor_match(tmp_path,monkeypatch):
    src=tmp_path/'source.wav';A.write_wav(src,np.ones(44100)*.5,22050)
    monkeypatch.setattr(V,'detect_onsets',lambda *a:([0.1],'fixture'))
    monkeypatch.setattr(V,'ChromaTool',lambda *a:lambda o:np.array([.03]*12))
    monkeypatch.setattr(sys,'argv',['vocal_tools','chops','--src',str(src),'--bpm','120','--chords','1:C','--out',str(tmp_path/'out.wav'),'--fx','none'])
    with pytest.raises(SystemExit,match='最低匹配分'):V.main()
    assert not (tmp_path/'out.wav').exists()


def test_vocal_uses_validated_midi(tmp_path):
    import logicsong as L
    s=L.Song(dict(title='vocal',bpm=120,sections=[dict(name='A',bars=1)],tracks=[dict(id='p')]))
    s.track('p').note(1,0,1,'C♯4');path=Path(s.write(tmp_path))
    notes,bpm=V.read_midi(path,'p')
    assert notes[0][2]==61 and bpm==120
    data=path.read_bytes();bad=data.replace(b'\x90\x3d\x50',b'\x90\x3d\x96')
    with pytest.raises(ValueError,match='0–127'):V.parse_smf(bad,path,'p')


def test_aubio_uses_only_aubiotrack(tmp_path,monkeypatch):
    calls=[]
    def lookup(name):
        calls.append(name);return '/tools/aubiotrack'
    from types import SimpleNamespace
    monkeypatch.setattr(A,'find_tool',lookup)
    def run(args,**kwargs):
        assert args[0]=='/tools/aubiotrack' and 'tempo' not in args
        return SimpleNamespace(returncode=0,stdout=' '.join(str(i*.5) for i in range(12)),stderr='')
    monkeypatch.setattr(A.subprocess,'run',run)
    assert A.aubio_tempo(np.zeros(22050))['bpm']==120
    assert calls==['aubiotrack']
