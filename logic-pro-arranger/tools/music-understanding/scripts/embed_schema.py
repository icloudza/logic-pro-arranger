"""Generate the native schema from the Python package SQL migrations."""
import json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
files = sorted((root / 'src/music_understanding/migrations').glob('*.sql'))
body = 'static const char *MIGRATIONS[] = {\n'
for n, path in enumerate(files, 1):
    sql = 'BEGIN IMMEDIATE;\n' + path.read_text() + f'\nPRAGMA user_version={n};\nCOMMIT;'
    body += json.dumps(sql) + ',\n'
body += '};\n'
out = root / 'build/schema.h'
out.parent.mkdir(exist_ok=True)
out.write_text(body)
