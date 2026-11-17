"""Build, verify, or restore a local code snapshot and separate private data archive.

Only standard-library modules are required for verification and restoration. No existing
file is overwritten. The data archive is a local backup, not an automatic redistribution grant.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]
CODE_ARCHIVE = 'project_code.zip'
DATA_ARCHIVE = 'LOCAL_private_data.zip'
MANIFEST = 'manifest.json'
HELPER = 'handoff.py'
REQUIRED = {
    'data/raw/alerts/airAlert_2026-10-26.json', 'data/raw/weather/kyiv_weather.json',
    'data/raw/geography/geoBoundaries-UKR-ADM2.geojson',
    'data/raw/geography/kyiv_districts_candidate_20260913.geojson',
    'data/raw/geography/boundary_reference_20261110/sportplaygrounds.json',
    'data/raw/geography/boundary_reference_20261110/manifest.json',
    'data/processed/alerts.csv', 'data/processed/kyiv_weather.csv', 'data/synthetic/deliveries.csv',
    'data/synthetic/benchmark/deliveries_10000.csv', 'data/synthetic/benchmark/deliveries_100000.csv',
    'data/synthetic/benchmark/deliveries_1000000.csv', 'data/synthetic/benchmark/manifest.json',
    'data/synthetic/spatial_research/delivery_points.csv', 'data/synthetic/spatial_research/manifest.json',
    'data/synthetic/outage_scenarios/manifest.json', 'data/synthetic/density_scenarios/manifest.json',
}


def sha_file(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def sha_stream(handle):
    return hashlib.file_digest(handle, 'sha256').hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Invalid archive path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')) or path.as_posix() != name:
        raise ValueError('Unsafe archive path')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    for part in path.parts:
        if (part.endswith(('.', ' ')) or part.split('.')[0].upper() in reserved or
            any(ord(c) < 32 or c in '<>"|?*' for c in part)):
            raise ValueError('Archive path is not portable to Windows')
    return name


def secret_path(name):
    path = PurePosixPath(name)
    parts = [part.lower() for part in path.parts]
    return any(part in ('.git', '.venv', '__pycache__') for part in parts) or any(
        part == '.env' or part.startswith('.env.') or part.endswith(('.pem', '.key')) for part in parts)


def git(root, *args):
    result = subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}', *args], cwd=root,
                            capture_output=True, check=True)
    return result.stdout


def clean_revision(root):
    if git(root, 'status', '--porcelain=v1', '-z'):
        raise ValueError('Commit or resolve all non-ignored changes before creating the bundle')
    revision = git(root, 'rev-parse', 'HEAD').decode('ascii').strip()
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Unexpected Git revision')
    tracked = [safe_name(p.decode('utf-8')) for p in git(root, 'ls-files', '-z').split(b'\0') if p]
    if not tracked or len(set(tracked)) != len(tracked) or any(secret_path(p) for p in tracked):
        raise ValueError('Tracked files include a disallowed path or are invalid')
    if any(p.startswith(('data/raw/', 'data/processed/', 'data/synthetic/')) and not p.endswith('/.gitkeep') for p in tracked):
        raise ValueError('Unexpected tracked dataset; review before building a transferable code archive')
    return revision, set(tracked)


def collect_data(root, tracked):
    result = {}
    for subtree in ('raw', 'processed', 'synthetic'):
        base = root / 'data' / subtree
        if not base.is_dir() or base.is_symlink() or base.is_junction():
            raise ValueError('Missing or linked data directory')
        for parent, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                path = Path(parent) / name
                if path.is_symlink() or path.is_junction():
                    raise ValueError('Linked data paths are not included')
                if not path.resolve().is_relative_to(root):
                    raise ValueError('Data path escapes project')
            for name in files:
                path = Path(parent) / name
                relative = safe_name(path.relative_to(root).as_posix())
                if relative in tracked:
                    continue
                if secret_path(relative):
                    raise ValueError('Secret-like file found under data; review before copying')
                result[relative] = {'bytes': path.stat().st_size, 'sha256': sha_file(path)}
    if not REQUIRED.issubset(result):
        raise FileNotFoundError('Required local data is missing: ' + ', '.join(sorted(REQUIRED - set(result))))
    return dict(sorted(result.items()))


def zip_inventory(path):
    inventory = {}
    folded = set()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            raw = info.filename[:-1] if info.is_dir() else info.filename
            name = safe_name(raw)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError('Archive links are not supported')
            if info.is_dir():
                continue
            if name.casefold() in folded or secret_path(name) or info.flag_bits & 1:
                raise ValueError('Duplicate, secret-like or encrypted archive entry')
            folded.add(name.casefold())
            with archive.open(info) as handle:
                checksum = sha_stream(handle)
            inventory[name] = {'bytes': info.file_size, 'sha256': checksum}
    return dict(sorted(inventory.items()))


def check_manifest(bundle):
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('format') != 'local-handoff-v1':
        raise ValueError('Unknown handoff format')
    if not isinstance(manifest.get('git_revision'), str) or not re.fullmatch(r'[0-9a-f]{40}', manifest['git_revision']):
        raise ValueError('Invalid recorded Git revision')
    archives = manifest.get('archives')
    if not isinstance(archives, dict) or set(archives) != {CODE_ARCHIVE, DATA_ARCHIVE}:
        raise ValueError('Unexpected archive list')
    all_paths = set()
    for archive_name, details in archives.items():
        path = bundle / archive_name
        if sha_file(path) != details['sha256'] or path.stat().st_size != details['bytes']:
            raise ValueError(f'Archive checksum or size mismatch: {archive_name}')
        inventory = zip_inventory(path)
        if inventory != details['files']:
            raise ValueError(f'Archive entries do not match manifest: {archive_name}')
        folded = {name.casefold() for name in inventory}
        if all_paths.intersection(folded):
            raise ValueError('Code and data archives overlap')
        all_paths.update(folded)
    data_paths = set(archives[DATA_ARCHIVE]['files'])
    if not REQUIRED.issubset(data_paths) or any(not p.startswith(('data/raw/', 'data/processed/', 'data/synthetic/')) for p in data_paths):
        raise ValueError('Private data inventory is missing required files or contains unexpected paths')
    if (archives[CODE_ARCHIVE]['files'].get('scripts/handoff_bundle.py', {}).get('sha256') != manifest['helper_sha256'] or
        sha_file(bundle / HELPER) != manifest['helper_sha256']):
        raise ValueError('Restore helper checksum mismatch')
    return manifest


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Bundle directory must be outside the project')
    if output.exists():
        raise FileExistsError('Bundle directory already exists')
    revision, tracked = clean_revision(root)
    data_files = collect_data(root, tracked)
    output.mkdir(parents=True, exist_ok=False)
    code_path, data_path = output / CODE_ARCHIVE, output / DATA_ARCHIVE
    with code_path.open('xb') as handle:
        subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}', 'archive', '--format=zip', revision],
                       cwd=root, stdout=handle, stderr=subprocess.PIPE, check=True)
    code_files = zip_inventory(code_path)
    if set(code_files) != tracked:
        raise ValueError('Git archive differs from the tracked-file list')
    if set(code_files).intersection(data_files):
        raise ValueError('Code and data paths overlap')
    with zipfile.ZipFile(data_path, mode='x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for name, metadata in data_files.items():
            path = root / name
            if path.stat().st_size != metadata['bytes'] or sha_file(path) != metadata['sha256']:
                raise RuntimeError('Input changed before archiving: ' + name)
            archive.write(path, arcname=name)
    if zip_inventory(data_path) != data_files:
        raise RuntimeError('Archived input files do not match source')
    if collect_data(root, tracked) != data_files or clean_revision(root)[0] != revision:
        raise RuntimeError('Source data or Git state changed during bundle creation')
    with zipfile.ZipFile(code_path) as archive:
        helper_data = archive.read('scripts/handoff_bundle.py')
    with (output / HELPER).open('xb') as handle:
        handle.write(helper_data)
    manifest = {'format': 'local-handoff-v1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'git_revision': revision, 'helper_sha256': sha_file(output / HELPER),
                'archives': {CODE_ARCHIVE: {'bytes': code_path.stat().st_size, 'sha256': sha_file(code_path), 'files': code_files},
                             DATA_ARCHIVE: {'bytes': data_path.stat().st_size, 'sha256': sha_file(data_path), 'files': data_files}},
                'notes': ['The private data archive includes the unofficial district candidate; redistribution rights remain unresolved.',
                          'No .env file, database volume, Git history or Python environment is included.',
                          'Checksums detect corruption but do not authenticate a bundle against deliberate replacement.']}
    with (output / MANIFEST).open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2); handle.write('\n')
    readme = '''# Комплект для відновлення\n\n`project_code.zip` — знімок коду з указаного коміту. `LOCAL_private_data.zip` — незашифрована локальна копія даних; вона містить неофіційний файл районів із невизначеними умовами поширення. Зберігайте її як локальну резервну копію та перевірте права перед передачею.\n\nПароль, `.env`, томи баз, історію Git і Python-середовище не включено. Інструкція запуску міститься в кодовому архіві: `docs/37_handoff.md`.\n\nПеревірити в PowerShell, перебуваючи в цій папці:\n\n```powershell\npy -3.13 .\\handoff.py verify --bundle .\n```\n\nВідновити в нову папку:\n\n```powershell\npy -3.13 .\\handoff.py restore --bundle . --destination C:\\Temp\\dwh-restored-01\n```\n\nКонтрольні суми виявляють випадкове пошкодження; вони не є цифровим підписом.\n'''
    (output / 'ПРОЧИТАЙТЕ.md').write_text(readme, encoding='utf-8', newline='\n')
    check_manifest(output)
    return manifest


def target_path(root, name):
    safe_name(name)
    target = root.joinpath(*PurePosixPath(name).parts)
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError('Linked restore path rejected')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Restore path escapes destination')
    return target


def extract_archive(archive_path, inventory, root):
    with zipfile.ZipFile(archive_path) as archive:
        for name, metadata in inventory.items():
            target = target_path(root, name)
            if target.exists():
                if target.is_file() and target.stat().st_size == metadata['bytes'] and sha_file(target) == metadata['sha256']:
                    continue
                raise FileExistsError('Existing file differs: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
            if target.stat().st_size != metadata['bytes'] or sha_file(target) != metadata['sha256']:
                raise RuntimeError('Restored file failed validation: ' + name)


def restore(bundle, destination, data_only=False):
    bundle, root = Path(bundle).resolve(), Path(destination).resolve()
    manifest = check_manifest(bundle)
    if root == bundle or root.is_relative_to(bundle):
        raise ValueError('Destination must be outside the bundle')
    if data_only:
        if not root.is_dir():
            raise FileNotFoundError('Existing Git checkout is required')
        revision, _ = clean_revision(root)
        if revision != manifest['git_revision']:
            raise ValueError('Git checkout must match the bundle revision exactly')
        names = [DATA_ARCHIVE]
    else:
        if root.exists():
            raise FileExistsError('Destination already exists')
        names = [CODE_ARCHIVE, DATA_ARCHIVE]
    # Preflight every destination before copying any file.
    for archive_name in names:
        for name, metadata in manifest['archives'][archive_name]['files'].items():
            target = target_path(root, name)
            if target.exists() and (not target.is_file() or target.stat().st_size != metadata['bytes'] or sha_file(target) != metadata['sha256']):
                raise FileExistsError('Existing file differs: ' + name)
    if not data_only:
        root.mkdir(parents=True, exist_ok=False)
    for archive_name in names:
        extract_archive(bundle / archive_name, manifest['archives'][archive_name]['files'], root)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('build', help='build two local archives from a clean Git checkout')
    create.add_argument('--project-root', type=Path, default=PROJECT)
    create.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify', help='check both archive hashes and each file')
    verify.add_argument('--bundle', type=Path, required=True)
    recover = sub.add_parser('restore', help='restore both archives into a new directory')
    recover.add_argument('--bundle', type=Path, required=True); recover.add_argument('--destination', type=Path, required=True)
    data = sub.add_parser('restore-data', help='safely restore data into the exact matching clean Git checkout')
    data.add_argument('--bundle', type=Path, required=True); data.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'build':
        manifest = build(args.project_root, args.output)
    elif args.action == 'verify':
        manifest = check_manifest(args.bundle)
    else:
        manifest = restore(args.bundle, args.destination, data_only=args.action == 'restore-data')
    print(json.dumps({'status': 'ok', 'action': args.action, 'git_revision': manifest['git_revision'],
                      'code_files': len(manifest['archives'][CODE_ARCHIVE]['files']),
                      'data_files': len(manifest['archives'][DATA_ARCHIVE]['files'])}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
