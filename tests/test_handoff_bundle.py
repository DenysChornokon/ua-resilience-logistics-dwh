import hashlib
import json
import stat
import zipfile

import pytest

from scripts import handoff_bundle as handoff


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sample_bundle(tmp_path):
    bundle = tmp_path / 'bundle'; bundle.mkdir()
    helper = b'# restore helper\n'
    code = bundle / handoff.CODE_ARCHIVE
    data = bundle / handoff.DATA_ARCHIVE
    with zipfile.ZipFile(code, 'w') as archive:
        archive.writestr('scripts/handoff_bundle.py', helper)
        archive.writestr('docs/readme.md', b'project')
    with zipfile.ZipFile(data, 'w') as archive:
        for name in sorted(handoff.REQUIRED):
            archive.writestr(name, name.encode('utf-8'))
    (bundle / handoff.HELPER).write_bytes(helper)
    manifest = {'format': 'local-handoff-v1', 'git_revision': 'a' * 40, 'helper_sha256': digest(helper), 'archives': {}}
    for path in (code, data):
        manifest['archives'][path.name] = {'bytes': path.stat().st_size, 'sha256': handoff.sha_file(path),
                                           'files': handoff.zip_inventory(path)}
    (bundle / handoff.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    return bundle, manifest


@pytest.mark.parametrize('name', ['../outside', 'data/../secret', '/absolute', 'C:/drive', 'data\\file',
                                  'data/CON.txt', 'data/file. ', 'data/a//b'])
def test_rejects_unsafe_or_nonportable_paths(name):
    with pytest.raises(ValueError):
        handoff.safe_name(name)


def test_verify_and_restore_into_new_directory(tmp_path):
    bundle, manifest = sample_bundle(tmp_path)
    assert handoff.check_manifest(bundle)['git_revision'] == 'a' * 40
    target = tmp_path / 'restored'
    handoff.restore(bundle, target)
    assert (target / 'docs/readme.md').read_bytes() == b'project'
    for name in handoff.REQUIRED:
        assert (target / name).read_bytes() == name.encode('utf-8')
    with pytest.raises(FileExistsError):
        handoff.restore(bundle, target)
    assert (target / 'docs/readme.md').read_bytes() == b'project'


def test_archive_corruption_is_detected(tmp_path):
    bundle, _ = sample_bundle(tmp_path)
    with (bundle / handoff.DATA_ARCHIVE).open('ab') as f:
        f.write(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        handoff.check_manifest(bundle)


def test_rejects_archive_symlink_and_case_collisions(tmp_path):
    path = tmp_path / 'unsafe.zip'
    info = zipfile.ZipInfo('data/link')
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(info, 'target')
    with pytest.raises(ValueError, match='links'):
        handoff.zip_inventory(path)
    path = tmp_path / 'collision.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('data/a.csv', 'a'); archive.writestr('DATA/A.csv', 'b')
    with pytest.raises(ValueError, match='Duplicate'):
        handoff.zip_inventory(path)


def test_data_restore_preflights_conflicts_without_overwriting(tmp_path, monkeypatch):
    bundle, _ = sample_bundle(tmp_path)
    target = tmp_path / 'checkout'; target.mkdir()
    existing = target / sorted(handoff.REQUIRED)[0]
    existing.parent.mkdir(parents=True)
    existing.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(handoff, 'clean_revision', lambda root: ('a' * 40, set()))
    with pytest.raises(FileExistsError, match='differs'):
        handoff.restore(bundle, target, data_only=True)
    assert existing.read_text(encoding='utf-8') == 'keep'
    assert len([p for p in target.rglob('*') if p.is_file()]) == 1
