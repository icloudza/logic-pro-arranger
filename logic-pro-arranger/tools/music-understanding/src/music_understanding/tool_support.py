"""Shared dependency discovery and atomic, non-destructive output."""
import contextlib
import errno
import hashlib
import json
import importlib
import os
from pathlib import Path
import shutil
import tempfile


def find_tool(name):
    override = os.environ.get("MUSIC_" + name.upper())
    if override:
        return override if Path(override).is_file() and os.access(override, os.X_OK) else None
    return shutil.which(name) or next((str(p) for d in ('/opt/homebrew/bin', '/usr/local/bin')
                                      if (p := Path(d) / name).is_file() and os.access(p, os.X_OK)), None)


class LazyNumpy:
    def __getattr__(self, name):
        try:
            module = importlib.import_module('numpy')
        except ImportError as exc:
            raise RuntimeError('需要 numpy；请使用已安装 numpy 的 Python，或运行 python3 -m pip install numpy') from exc
        return getattr(module, name)


def guard_out(out, *inputs, force=False):
    out = Path(out).expanduser()
    for source in filter(None, inputs):
        source = Path(source).expanduser()
        if out.resolve() == source.resolve() or (out.exists() and source.exists() and os.path.samefile(out, source)):
            raise ValueError(f'输出不能覆盖输入：{out}')
    if out.is_symlink():
        raise ValueError(f'输出不能是软链：{out}')
    if out.exists() and not force:
        raise FileExistsError(f'输出已存在，请换名：{out}')
    return out


def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def publish(tmp, out, force=False):
    """No-clobber publication; exclusive copy on filesystems without hard links."""
    tmp, out = Path(tmp), Path(out)
    if force:
        os.replace(tmp, out)
        return
    try:
        os.link(tmp, out)
    except FileExistsError:
        raise FileExistsError(f'输出已存在：{out}') from None
    except OSError as exc:
        if exc.errno not in {errno.EPERM, errno.ENOSYS, errno.EOPNOTSUPP, errno.EXDEV, 45}:
            raise
        # Exclusive creation retains no-clobber guarantees. Unlike rename/link,
        # the completed file becomes visible progressively during this fallback.
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(fd, 'wb') as dst, tmp.open('rb') as src:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
        except BaseException:
            out.unlink(missing_ok=True)
            raise


def receipt_path(out):
    out = Path(out)
    return out.with_name('.' + out.name + '.build.json')


def owned_output(out, owner):
    """Only exact, unchanged output from this producer can be rebuilt."""
    out = Path(out)
    if not owner or not out.is_file() or out.is_symlink():
        return False
    try:
        receipt = json.loads(receipt_path(out).read_text())
        return receipt.get('owner') == str(owner) and receipt.get('sha256') == file_digest(out)
    except (OSError, ValueError):
        return False


@contextlib.contextmanager
def output_path(out, *inputs, force=False, owner=None):
    out = Path(out).expanduser()
    rebuild = owned_output(out, owner)
    try:
        out = guard_out(out, *inputs, force=force or rebuild)
    except FileExistsError:
        if not owner:
            raise
        try:
            same_owner = json.loads(receipt_path(out).read_text()).get('owner') == str(owner)
        except (OSError, ValueError):
            same_owner = False
        why = '上次生成后被改动过（与构建记录不一致）' if same_owner else '没有本工具对应的构建记录'
        raise FileExistsError(f'输出已存在且{why}，未覆盖：{out}。'
                              '换一个输出名；生成脚本和试听确认不再需要它后可加 --force') from None
    receipt = receipt_path(out) if owner else None
    if receipt and receipt.is_symlink():
        raise ValueError(f'构建记录不能是软链：{receipt}')
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + out.stem + '-', suffix=out.suffix, dir=out.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        # mkstemp is private by default; published artifacts follow normal umask.
        mask = os.umask(0)
        os.umask(mask)
        tmp.chmod((out.stat().st_mode & 0o777) if out.exists() else (0o666 & ~mask))
        yield tmp
        if rebuild and not force and not owned_output(out, owner):
            raise FileExistsError(f'生成期间输出被外部修改：{out}')
        guard_out(out, *inputs, force=force or rebuild)
        digest = file_digest(tmp) if owner else None
        publish(tmp, out, force=force or rebuild)
        if receipt:
            write_text(receipt, json.dumps({'owner': str(owner), 'sha256': digest}), force=True)
    finally:
        tmp.unlink(missing_ok=True)


def write_text(out, value, *inputs, force=False, owner=None):
    with output_path(out, *inputs, force=force, owner=owner) as temp:
        temp.write_text(value, encoding='utf-8')


def config_dir():
    return Path(os.environ.get('LOGIC_ARRANGER_CONFIG', '~/.config/logic-pro-arranger')).expanduser()


@contextlib.contextmanager
def audio_input(path):
    """Unwrap RIFF Vorbis data for ffmpeg; never rewrite source audio."""
    import struct
    path = Path(path)
    payload = None
    with path.open('rb') as src:
        head = src.read(12)
        if head[:4] == b'RIFF' and head[8:] == b'WAVE':
            codec = None
            chunks = []
            size = path.stat().st_size
            while src.tell() + 8 <= size:
                tag, n = struct.unpack('<4sI', src.read(8))
                at = src.tell()
                if at + n > size:
                    raise ValueError(f'RIFF 块截断：{path}')
                if tag == b'fmt ' and n >= 2:
                    codec = struct.unpack('<H', src.read(2))[0]
                elif tag == b'data':
                    chunks.append((at, n))
                src.seek(at+n+n%2)
            if codec in (0x674f, 0x6750, 0x6751, 0x676f, 0x6770, 0x6771):
                parts = []
                for at, n in chunks:
                    src.seek(at); parts.append(src.read(n))
                payload = b''.join(parts)
                if not payload.startswith(b'OggS'):
                    raise ValueError(f'RIFF Vorbis 格式 {codec:#x} 缺少 Ogg 页，无法直接解包：{path}')
    if payload is None:
        yield path
    else:
        with tempfile.TemporaryDirectory(prefix='music-ogg-') as td:
            unwrapped = Path(td) / 'audio.ogg'
            unwrapped.write_bytes(payload)
            yield unwrapped


def normalized_audio(func):
    import functools
    @functools.wraps(func)
    def wrapped(path, *args, **kwargs):
        with audio_input(path) as source:
            return func(str(source), *args, **kwargs)
    return wrapped


def cached_digest(path):
    """Persistent hash cache keyed by path, inode, size, mtime and ctime."""
    path = Path(path).resolve()
    stat = path.stat()
    key = [str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    cache = Path(os.environ.get('MUSIC_HASH_CACHE', '~/.cache/music-understanding/hashes')).expanduser()
    target = cache / (hashlib.sha256(str(path).encode()).hexdigest() + '.json')
    try:
        entry = json.loads(target.read_text())
        if entry['key'] == key:
            return entry['sha256']
    except (OSError, ValueError, KeyError):
        pass
    value = file_digest(path)
    after = path.stat()
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != tuple(key[-3:]):
        raise ValueError(f'计算哈希时文件改变：{path}')
    write_text(target, json.dumps({'key': key, 'sha256': value}), force=True)
    return value


def probe_audio(path):
    import subprocess
    exe = find_tool('ffprobe')
    if not exe:
        raise RuntimeError('缺少 ffprobe；安装 FFmpeg 或设置 MUSIC_FFPROBE')
    with audio_input(path) as source:
        result = subprocess.run([exe, '-v', 'error', '-select_streams', 'a:0',
            '-show_streams', '-show_format', '-of', 'json', str(source)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(f'ffprobe 无法读取 {path}：{result.stderr.strip()[-800:]}')
    data = json.loads(result.stdout)
    if not data.get('streams'):
        raise ValueError(f'文件不含音频流：{path}')
    return data
