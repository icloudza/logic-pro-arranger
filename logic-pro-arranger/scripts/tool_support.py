"""Standard-library I/O shared with the bundled audio worker."""
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/music-understanding/src'))
from music_understanding.tool_support import (find_tool, guard_out, output_path, write_text,
    LazyNumpy, publish, file_digest, owned_output, config_dir, audio_input, normalized_audio, probe_audio)


def embedded_library(body=None):
    """Embed only MIDI-required I/O from the shared implementation."""
    import ast
    if not body:
        source = Path(__file__).with_name('logicsong.py').read_text(encoding='utf-8')
        body = source.split('# ==== LIB START ====', 1)[1].split('# ==== LIB END ====', 1)[0].strip()
    from music_understanding import tool_support
    source = Path(tool_support.__file__).read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    needed = {'guard_out', 'file_digest', 'publish', 'receipt_path', 'owned_output', 'output_path', 'write_text'}
    parts = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or isinstance(node, ast.FunctionDef) and node.name in needed:
            start = min([node.lineno] + [x.lineno for x in getattr(node, 'decorator_list', [])])
            parts.append(''.join(lines[start-1:node.end_lineno]))
    return body.replace('from tool_support import output_path', '\n'.join(parts))
