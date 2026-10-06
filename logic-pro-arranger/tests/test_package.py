import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def packager():
    # 打包器只存在于源码仓库，安装后的 skill 仍可单独运行其余测试。
    script=ROOT/'package_skill.py'
    if not script.exists():
        pytest.skip('打包测试需在源码仓库运行')
    spec=importlib.util.spec_from_file_location('packager',script)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_links():
    skill=ROOT/'logic-pro-arranger'
    for path in [skill/'SKILL.md',*list((skill/'references').rglob('*.md'))]:
        for match in re.finditer(r'\]\(([^)]+)\)',path.read_text()):
            target=match[1].split('#')[0]
            if target and not re.match(r'\w+://',target):
                assert (path.parent/target).exists(),(path,target)


@pytest.mark.parametrize('archive_format', ['plugin', 'skill'])
def test_source_distribution(packager, tmp_path, archive_format):
    target=tmp_path/'package.zip'
    packager.package(target, archive_format)
    prefix='skills/logic-pro-arranger' if archive_format == 'plugin' else 'logic-pro-arranger'
    with zipfile.ZipFile(target) as z:
        names=z.namelist()
        assert f'{prefix}/tools/music-understanding/src/music_understanding/engine.py' in names
        assert not any(set(Path(n).parts)&{'local','build','.venv','__pycache__'} for n in names)
        assert not any(Path(n).is_absolute() or '..' in Path(n).parts for n in names)
        if archive_format == 'plugin':
            for path in packager.MANIFESTS:
                manifest=json.loads(z.read(path))
                source_manifest=json.loads((ROOT/path).read_text())
                assert manifest.pop('skills') == './skills/'
                source_skills=source_manifest.pop('skills')
                if isinstance(source_skills,str):
                    source_skills=[source_skills]
                assert manifest == source_manifest
                assert all((ROOT/p/'SKILL.md').is_file() for p in source_skills)
            marketplace=json.loads(z.read('.agents/plugins/marketplace.json'))
            assert marketplace['plugins'][0]['source'] == {'source':'local','path':'./'}
            assert marketplace['plugins'][0]['name'] == manifest['name']
            assert f'{prefix}/SKILL.md' in names
            assert f'{prefix}/agents/openai.yaml' in names
            assert 'logic-pro-arranger/SKILL.md' not in names
        else:
            assert '.claude-plugin/plugin.json' not in names
            assert '.codex-plugin/plugin.json' not in names
        # 两种格式都保留技能内部的相对路径与内容。
        for name in names:
            if name.startswith(prefix+'/'):
                assert z.read(name) == (ROOT/'logic-pro-arranger'/name[len(prefix)+1:]).read_bytes()
        z.extractall(tmp_path/'install')
    installed=tmp_path/'install'/prefix
    for script in (installed/'scripts').glob('*.py'):
        ast.parse(script.read_text())
    # 在安装目录之外启动，且不借用本机安装的音频包或 PYTHONPATH。
    result=subprocess.run([
        sys.executable, '-E', '-S', '-B', str(installed/'scripts/new_song.py'),
        '--title', '打包验证', '--key', 'F minor', '--bpm', '88', '--root', str(tmp_path/'songs'),
    ], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout+result.stderr
    generated=tmp_path/'songs/打包验证/打包验证_生成脚本.py'
    assert generated.is_file()
    ast.parse(generated.read_text())


def test_default_cli_builds_plugin(packager, tmp_path):
    target=tmp_path/'arranger.plugin'
    result=subprocess.run([
        sys.executable, str(ROOT/'package_skill.py'), '--out', str(target),
    ], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout+result.stderr
    with zipfile.ZipFile(target) as z:
        assert '.claude-plugin/plugin.json' in z.namelist()
        assert 'skills/logic-pro-arranger/SKILL.md' in z.namelist()
    before=target.read_bytes()
    with pytest.raises(FileExistsError):
        packager.package(target)
    assert target.read_bytes() == before


@pytest.mark.parametrize('archive_format', ['plugin', 'skill'])
def test_runtime_and_local_files_excluded(packager, monkeypatch, tmp_path, archive_format):
    source=tmp_path/'source'
    skill=source/'logic-pro-arranger'
    skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('skill')
    for directory in ['local', '.venv', 'build', '__pycache__', '.pytest_cache', '.git', 'package.egg-info']:
        (skill/directory).mkdir()
        (skill/directory/'private').write_text('excluded')
    for filename in ['.DS_Store', 'cache.pyc', 'cache.pyo']:
        (skill/filename).write_text('excluded')
    (skill/'linked-file').symlink_to(skill/'SKILL.md')
    (skill/'linked-directory').symlink_to(skill/'local', target_is_directory=True)
    for path in (*packager.MANIFESTS,'.agents/plugins/marketplace.json'):
        (source/path).parent.mkdir(parents=True,exist_ok=True)
        (source/path).write_bytes((ROOT/path).read_bytes())
    monkeypatch.setattr(packager, 'ROOT', source)
    target=tmp_path/'filtered.zip'
    packager.package(target, archive_format)
    with zipfile.ZipFile(target) as z:
        expected={'logic-pro-arranger/SKILL.md'} if archive_format == 'skill' else {
            *packager.MANIFESTS, '.agents/plugins/marketplace.json', 'skills/logic-pro-arranger/SKILL.md',
        }
        assert set(z.namelist()) == expected


def test_release_artifacts(packager, tmp_path):
    version=json.loads((ROOT/packager.MANIFESTS[0]).read_text())['version']
    output=packager.release(tmp_path/'release','v'+version)
    plugin=output/f'logic-pro-arranger-{version}.plugin'
    assert plugin.read_bytes() == plugin.with_suffix('.zip').read_bytes()
    checksums=(output/'SHA256SUMS').read_text().splitlines()
    assert len(checksums) == 3
    for entry in checksums:
        digest,name=entry.split('  ')
        assert hashlib.sha256((output/name).read_bytes()).hexdigest() == digest
    assert (output/f'logic-pro-arranger-skill-{version}.zip').exists()
    assert 'Claude Desktop' in (output/'RELEASE_NOTES.md').read_text()
    assert 'Codex' in (output/'RELEASE_NOTES.md').read_text()
    with pytest.raises(FileExistsError):
        packager.release(output,'v'+version)


@pytest.mark.parametrize('tag',['0.2.1','v999.0.0','v1.0.0/../bad'])
def test_release_rejects_wrong_tag(packager, tmp_path, tag):
    with pytest.raises(ValueError):
        packager.release(tmp_path/'release',tag)
    assert not (tmp_path/'release').exists()


def test_release_rejects_mismatched_manifests(packager, monkeypatch, tmp_path):
    source=tmp_path/'source'
    for path in packager.MANIFESTS:
        manifest=json.loads((ROOT/path).read_text())
        manifest['version']='1.0.0' if path.startswith('.claude-plugin') else '1.0.1'
        (source/path).parent.mkdir(parents=True)
        (source/path).write_text(json.dumps(manifest))
    monkeypatch.setattr(packager,'ROOT',source)
    with pytest.raises(ValueError,match='清单的版本一致'):
        packager.release(tmp_path/'release','v1.0.0')
    assert not (tmp_path/'release').exists()
