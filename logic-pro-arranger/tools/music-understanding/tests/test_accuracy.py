import json
from pathlib import Path
import subprocess

import numpy as np
import soundfile as sf
from music_understanding import features as F
from music_understanding.engine import analyze, run_task
from music_understanding.store import Store

ROOT=Path(__file__).resolve().parents[1]


def test_original_peak_and_clipping(tmp_path):
    (tmp_path/'inputs').mkdir()
    src=tmp_path/'inputs/source.wav'
    # Square-wave discontinuities overshoot when resampled; opposite channels also test downmix.
    x=np.tile(np.r_[np.full(16,.999),np.full(16,-.999)],300).astype('float32')
    stereo=np.column_stack([x,-x]);sf.write(src,stereo,48000,subtype='FLOAT')
    store=Store(tmp_path/'cache')
    result=analyze(store,str(src),tasks=['envelope']);m=result['tasks'][0]['summary']
    assert result['status']=='complete'
    assert abs(m['peak_dbfs']-20*np.log10(.999))<1e-5
    assert m['clipping_sample_fraction']==0 and m['sample_rate']==48000
    c=json.loads(subprocess.check_output([str(ROOT/'build/music'),'dsp',str(src)]))
    assert abs(c['peak_dbfs']-m['peak_dbfs'])<1e-5 and c['clipping_sample_fraction']==0
    assert abs(c['rms_dbfs']-m['rms_dbfs'])<1e-5
    store.close()


def test_onsets_early_and_sustain(tmp_path):
    sr=22050;t=np.arange(sr*2)/sr
    carrier=.5*np.sin(2*np.pi*65.4064*t)
    x=carrier.copy();x[:round(.008*sr)]=0
    summary,events=F.onsets(x,sr,0,tmp_path)
    assert summary['count']==1 and abs(summary['times'][0]-.008)<.003
    assert events[0]['time_error_s'] is None
    x=np.zeros(sr*2)
    for at in (.008,.5,1.,1.5):
        i=round(at*sr);u=np.arange(round(.2*sr))/sr
        x[i:i+len(u)]=np.sin(2*np.pi*90*u)*np.exp(-u*40)
    summary,_=F.onsets(x,sr,0,tmp_path)
    assert len(summary['times'])==4
    assert np.max(np.abs(np.array(summary['times'])-[.008,.5,1,1.5]))<.003


def test_canonical_notes_and_raw_views(tmp_path):
    (tmp_path/'inputs').mkdir()
    src=tmp_path/'inputs/source.wav';sf.write(src,np.zeros(22050),22050)
    store=Store(tmp_path/'cache');asset=store.register(src)['id']
    def save(task,key):
        e=F.event('note',0,.2,{'midi':60},task)
        store.save_run(asset,task,key,'complete',{},[e])
    save('pitch','pitch-run');save('notes','notes-run')
    assert len(store.events(asset,kind='note'))==1
    assert len(store.events(asset,kind='note',task='pitch'))==1
    for flags in ([],['--task','pitch']):
        raw=subprocess.check_output([str(ROOT/'build/music'),'--home',str(store.root),'query',asset,'events','--type','note',*flags])
        assert len(json.loads(raw)['events'])==1
    store.close()


def test_conflicting_beat_model_is_not_alignment_grid(tmp_path,monkeypatch):
    from music_understanding import models
    monkeypatch.setattr(models,'beat_model',lambda *a:({'bpm':187.5,'beats':[0,.32,.64], 'candidates':[187.5], 'reasons':[]},
        [F.event('beat',.32,.32,{},'fixture')]))
    y=np.sin(np.arange(44100)*.1)*.3
    result,events=run_task('rhythm',y,22050,0,tmp_path,{},dict(beat_backend='beat-this',bpm=130),{})
    assert result['status']=='conflict' and result['beats'] and result['grid'] is None
    assert events[0]['type']=='beat_candidate'
    assert result['bpm']==187.5


def test_ensemble_preserves_separate_contours(tmp_path,monkeypatch):
    from music_understanding import models,native
    def result(folder,value):
        p=Path(folder)/'f0.npy';np.save(p,[value])
        return {'root':{'midi':60},'contour':{'path':str(p)}},[F.event('note',0,.5,{'midi':60},'fixture')]
    monkeypatch.setattr(native,'pitch',lambda y,sr,origin,folder,*a:result(folder,1))
    monkeypatch.setattr(models,'crepe',lambda y,sr,origin,folder,*a:result(folder,2))
    summary,ev=run_task('pitch',np.zeros(22050),22050,0,tmp_path,{},dict(pitch_backend='ensemble'),{})
    assert [e['type'] for e in ev]==['note','note_alternative']
    a=summary['observations']['yin-c']['contour']['path'];b=summary['observations']['torchcrepe']['contour']['path']
    assert a!=b and np.load(a)[0]==1 and np.load(b)[0]==2


def test_half_double_tempo_phase_supported_and_retained(tmp_path,monkeypatch):
    from music_understanding import models
    beats=np.arange(0,5,60/130).tolist()
    monkeypatch.setattr(models,'beat_model',lambda *a:({'bpm':130,'beats':beats,'candidates':[65,130,260],'reasons':[]},[]))
    monkeypatch.setattr(F,'rhythm',lambda *a:({'bpm':65,'beats':beats[::2]},[]))
    result,_=run_task('rhythm',np.zeros(22050*5),22050,0,tmp_path,{},dict(beat_backend='beat-this',bpm=65),{})
    assert result['usable_for_alignment'] and not result['cross_check']['conflict']
    assert result['cross_check']['model_to_reference_factor']==2 and result['grid']['beats']==beats
    monkeypatch.setattr(F,'rhythm',lambda *a:({'bpm':65,'beats':[t+.2 for t in beats[::2]]},[]))
    result,_=run_task('rhythm',np.zeros(22050*5),22050,0,tmp_path,{},dict(beat_backend='beat-this',bpm=65),{})
    assert not result['usable_for_alignment'] and result['beats']==beats
    assert 'insufficient_phase_or_interval_agreement' in result['reasons']


def test_alignment_requires_audibly_tight_phase(tmp_path,monkeypatch):
    from music_understanding import models
    beats=np.arange(0,5,60/130).tolist()
    monkeypatch.setattr(models,'beat_model',lambda *a:({'bpm':130,'beats':beats,'candidates':[130],'reasons':[]},[]))
    for shift,usable in ((.01,True),(.04,False)):
        monkeypatch.setattr(F,'rhythm',lambda *a,s=shift:({'bpm':130,'beats':[t+s for t in beats]},[]))
        result,_=run_task('rhythm',np.zeros(22050*5),22050,0,tmp_path,{},dict(beat_backend='beat-this',bpm=130),{})
        assert result['usable_for_alignment'] is usable and result['cross_check']['phase_tolerance_s']==.025


def test_model_hash_reuse_and_invalidation(tmp_path,monkeypatch):
    from music_understanding import tool_support as io
    monkeypatch.setenv('MUSIC_HASH_CACHE',str(tmp_path/'hashes'))
    model=tmp_path/'model';model.write_bytes(b'first')
    real=io.file_digest;calls=[]
    monkeypatch.setattr(io,'file_digest',lambda p:(calls.append(str(p)),real(p))[1])
    first=io.cached_digest(model); assert io.cached_digest(model)==first
    assert len([p for p in calls if p==str(model)])==1
    model.write_bytes(b'other')
    assert io.cached_digest(model)!=first


def test_database_migration_shared_with_native(tmp_path):
    import sqlite3
    root=tmp_path/'db';root.mkdir()
    conn=sqlite3.connect(root/'catalog.sqlite')
    sql=(ROOT/'src/music_understanding/migrations/001.sql').read_text()
    conn.executescript(sql+'\nPRAGMA user_version=1;');conn.close()
    subprocess.run([str(ROOT/'build/music'),'--home',str(root),'search','--text','none'],check=True,capture_output=True)
    store=Store(root)
    assert store.db.execute('PRAGMA user_version').fetchone()[0]==2
    indexes={r[1] for r in store.db.execute('PRAGMA index_list(events)')}
    assert 'event_run' in indexes
    assert 'event_run' in str([tuple(r) for r in store.db.execute("EXPLAIN QUERY PLAN DELETE FROM events WHERE run_id='example'")])
    store.close()
