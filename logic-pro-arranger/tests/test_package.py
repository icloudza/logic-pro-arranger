import ast
from pathlib import Path
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]


def test_reference_links():
    skill=ROOT/'logic-pro-arranger'
    for path in [skill/'SKILL.md',*list((skill/'references').rglob('*.md'))]:
        for match in re.finditer(r'\]\(([^)]+)\)',path.read_text()):
            target=match[1].split('#')[0]
            if target and not re.match(r'\w+://',target):
                assert (path.parent/target).exists(),(path,target)


def test_source_distribution(tmp_path):
    # Root packager is a maintainer tool, not part of the installed skill.
    script=ROOT/'package_skill.py'
    if not script.exists():return
    import importlib.util
    spec=importlib.util.spec_from_file_location('packager',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    target=tmp_path/'skill.zip';module.package(target)
    with zipfile.ZipFile(target) as z:
        names=z.namelist()
        assert 'logic-pro-arranger/tools/music-understanding/src/music_understanding/engine.py' in names
        assert not any(set(Path(n).parts)&{'local','build','.venv','__pycache__'} for n in names)
        z.extractall(tmp_path/'install')
    for script in (tmp_path/'install/logic-pro-arranger/scripts').glob('*.py'):
        ast.parse(script.read_text())
