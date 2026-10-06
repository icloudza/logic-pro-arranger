import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import check_midi as C
import logicsong as L
from upgrade_song import upgrade
from new_song import build


def tree_body(source,name):
    return ast.dump(next(n for n in ast.parse(source).body if getattr(n,'name',None)==name or
                        isinstance(n,ast.Assign) and any(getattr(t,'id',None)==name for t in n.targets)),include_attributes=False)


def test_generated_roundtrip_and_upgrade(tmp_path):
    lib=(SCRIPTS/'logicsong.py').read_text().split('# ==== LIB START ====')[1].split('# ==== LIB END ====')[0]
    original=build('draft','custom','','C major',120,lib)
    original=original.replace('    pass\n\n\n# 内嵌','    song.track("lead").line(1,0,"C♯4:1 E4:1 G4:2")\n\n\n# 内嵌')
    original=original.replace(f'LIB_VERSION = "{L.LIB_VERSION}"','LIB_VERSION = "0.1.0"')
    script=tmp_path/'original.py';script.write_text(original)
    upgraded=tmp_path/'upgraded.py'
    subprocess.run([sys.executable,str(SCRIPTS/'upgrade_song.py'),str(script),'--out',str(upgraded)],check=True)
    assert script.read_text()==original
    for name in ('SPEC','compose'):assert tree_body(original,name)==tree_body(upgraded.read_text(),name)
    assert subprocess.check_output([sys.executable,str(upgraded),'--version'],text=True).strip()==L.LIB_VERSION
    subprocess.run([sys.executable,str(upgraded)],check=True,capture_output=True)
    mid=tmp_path/'draft.mid';m=C.Midi(mid)
    assert [n[3] for n in m.tracks[1]['notes']]==[61,64,67]
    before=mid.read_bytes()
    assert subprocess.run([sys.executable,str(upgraded)],capture_output=True).returncode==0
    assert mid.read_bytes()==before
    # Legacy generated scripts had a comment but no markers around the embedded library.
    legacy=original.replace('# ==== LIB START ====\n','').replace('# ==== LIB END ====\n','')
    assert tree_body(upgrade(legacy),'compose')==tree_body(legacy,'compose')
    # A copied standalone generation script needs no skill on PYTHONPATH.
    result=subprocess.run([sys.executable,'-I',str(upgraded),'--out',str(tmp_path/'elsewhere')],capture_output=True,text=True)
    assert result.returncode==0, result.stderr


def test_partial_chord_bar_is_silent_after_duration():
    song=L.Song(dict(title='partial',bpm=120,sections=[dict(name='A',bars=1,chords='C:1')],tracks=[]))
    assert song.chord(1,.5)=='C'
    assert song.chord(1,2) is None


def test_version_diagnostic_for_legacy_script(tmp_path):
    path=tmp_path/'legacy.py';path.write_text('SPEC = {"title":"x"}\n')
    spec,note=C.load_spec(str(path))
    assert spec['title']=='x' and '版本' in note


def test_local_import_profile_optional(tmp_path):
    spec=dict(title='strings',bpm=120,key='C major',sections=[dict(name='A',bars=1,chords='C')],tracks=[dict(id='p',program=40)])
    song=L.Song(spec);song.track('p').note(1,0,1,'C4');mid=song.write(tmp_path)
    m=C.Midi(mid);report=C.analyze(m,spec)
    checks=C.run_checks(m,report,spec,None)
    assert not any(c['id']=='T1' for c in checks)
