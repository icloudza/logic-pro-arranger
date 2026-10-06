import copy
import struct
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import logicsong as L
import check_midi as C
from tool_support import output_path


def spec(**kw):
    return dict(title='test', bpm=120, key='C major', sections=[dict(name='A', bars=4, chords='C')],
                tracks=[dict(id='p', channel=1, program=0)], **kw)


@pytest.mark.parametrize('value', [-1, 1.2, 2**28, float('nan')])
def test_vlq_rejects_invalid(value):
    with pytest.raises(ValueError): L._vlq(value)


@pytest.mark.parametrize('call', [lambda t: t.cc(1,-1,7,80), lambda t: t.ramp(11,(1,-1),(2,0),10,100),
                                 lambda t: t.bend(1,-1,1), lambda t: t.pedal(1,-1),
                                 lambda t: t.line(1,-1,'C4:1')])
def test_negative_time(call):
    with pytest.raises(ValueError): call(L.Song(spec()).track('p'))


@pytest.mark.parametrize('field,value', [('volume',150),('program',130),('pan',-1),('channel',1.5)])
def test_invalid_config(field, value):
    s=spec();s['tracks'][0][field]=value
    with pytest.raises(ValueError): L.Song(s)


def test_transposition_must_fail_before_write(tmp_path):
    s=spec();s['tracks'].append(dict(id='copy',channel=2,copy_of='p',transpose=24))
    song=L.Song(s);song.track('p').note(1,0,1,'C9')
    with pytest.raises(ValueError): song.write(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('field,value', [('title','../x'),('meter',[6,3]),('bpm',float('inf'))])
def test_spec_validation(field,value):
    s=spec();s[field]=value
    with pytest.raises(ValueError): L.Song(s)


def test_chords_and_pad():
    s=spec();s['sections'][0]['chords']='Fm:3 C7:3'
    with pytest.raises(ValueError): L.Song(s)
    s['sections'][0]['chords']='Fm % | %'
    song=L.Song(s)
    assert song.chords_in(1)==[(0.,'Fm',2.),(2.,'Fm',2.)]
    assert song.chords_in(2)==song.chords_in(1)
    song.track('p').pad([1,3],48,72)
    assert all(not(a < song.tick(2) < b) for a,b,_,_ in song.track('p').notes)
    assert L.key_signature('Ab minor')==(-7,1)
    assert C._nn('C♯4')==L.nn('C♯4')==61


def test_no_overwrite(tmp_path):
    song=L.Song(spec());p=Path(song.write(tmp_path));original=p.read_bytes()
    with pytest.raises(FileExistsError):song.write(tmp_path)
    assert p.read_bytes()==original
    source=tmp_path/'source.wav';source.write_bytes(b'master')
    alias=tmp_path/'alias.wav';alias.hardlink_to(source)
    with pytest.raises(ValueError):
        with output_path(alias,source,force=True):pass
    assert source.read_bytes()==b'master'
    with pytest.raises(RuntimeError):
        with output_path(tmp_path/'failed.wav') as temp:
            temp.write_bytes(b'partial');raise RuntimeError()
    assert not (tmp_path/'failed.wav').exists()


@pytest.mark.parametrize('event', [b'\x00\x90\x80\x40',b'\x00\xc0\x82',b'\x00\xb0\x07\x96',b'\x81\x81\x81\x81',b'\x00\x90\x40'])
def test_invalid_bytes(event,tmp_path):
    p=tmp_path/'bad.mid';body=event+b'\x00\xff\x2f\x00'
    p.write_bytes(b'MThd'+struct.pack('>IHHH',6,0,1,480)+b'MTrk'+struct.pack('>I',len(body))+body)
    with pytest.raises(ValueError):C.Midi(p)
    r=subprocess.run([sys.executable,str(SCRIPTS/'check_midi.py'),str(p)],capture_output=True,text=True,timeout=5)
    assert r.returncode==2 and 'ERROR' in r.stderr and 'Traceback' not in r.stderr


def test_truncated_and_syntax_error(tmp_path):
    song=L.Song(spec());p=Path(song.write(tmp_path));data=p.read_bytes()
    for n in range(len(data)):
        p.write_bytes(data[:n])
        with pytest.raises(ValueError):C.Midi(p)
    p.write_bytes(data)
    bad=tmp_path/'bad.py';bad.write_text('SPEC = [')
    r=subprocess.run([sys.executable,str(SCRIPTS/'check_midi.py'),str(p),'--script',str(bad)],capture_output=True,text=True)
    assert r.returncode==2 and 'Traceback' not in r.stderr


@pytest.mark.parametrize('change',['velocity','program','volume','pan','cc_before_section','bend'])
def test_locked_performance_changes(change,tmp_path):
    def build(name,modified):
        s=spec();s['title']=name;s['sections']=[dict(name='A',bars=1,chords='C'),dict(name='B',bars=1,chords='C')]
        if modified and change in ('program','volume','pan'): s['tracks'][0][change]=30
        song=L.Song(s);t=song.track('p');t.note(2,0,1,'C4',30 if modified and change=='velocity' else 80)
        if modified and change=='cc_before_section':t.cc(1,1,7,30)
        if modified and change=='bend':t.bend(1,2,1)
        return C.Midi(song.write(tmp_path))
    old,new=build('old',False),build('new',True)
    table,locks,changed=C.compare(new,old,C.analyze(new,None),C.analyze(old,None),dict(locked=['p@B']))
    assert table[('p','B')]!='=' and locks[0]['sev']=='ERROR'


def test_shared_channel_control_affects_locked_track(tmp_path):
    def build(title,volume):
        data=spec();data['title']=title;data['tracks'].append(dict(id='controller',channel=1,volume=volume))
        song=L.Song(data);song.track('p').note(1,0,1,'C4')
        return C.Midi(song.write(tmp_path))
    old,new=build('a',100),build('b',20)
    _,locks,_=C.compare(new,old,C.analyze(new,None),C.analyze(old,None),dict(locked=['p@A']))
    assert locks[0]['sev']=='ERROR'
