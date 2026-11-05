import hashlib
import json

import pytest
from scripts import load_benchmark_datasets as loader


def fixture_files(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, 'SIZES', (2, 3, 4))
    items = []
    for n in loader.SIZES:
        content = (','.join(loader.FIELDS) + '\n' + 'x\n' * n).encode()
        name = f'deliveries_{n}.csv'
        (tmp_path / name).write_bytes(content)
        items.append({'file': name, 'rows': n, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    manifest = {'generator': 'generate_benchmark_deliveries_v1', 'synthetic': True, 'seed': 42, 'datasets': items}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


def test_manifest_and_checksums(tmp_path, monkeypatch):
    fixture_files(tmp_path, monkeypatch)
    seed, files = loader.inspect_sources(tmp_path)
    assert seed == 42 and set(files) == {2, 3, 4}
    (tmp_path / 'deliveries_3.csv').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='Checksum'):
        loader.inspect_sources(tmp_path)


def test_unexpected_filename_is_rejected(tmp_path, monkeypatch):
    manifest = fixture_files(tmp_path, monkeypatch)
    manifest['datasets'][0]['file'] = '../other.csv'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='filename'):
        loader.inspect_sources(tmp_path)


def test_duplicate_size_is_rejected(tmp_path, monkeypatch):
    manifest = fixture_files(tmp_path, monkeypatch)
    manifest['datasets'][1] = manifest['datasets'][0]
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate'):
        loader.inspect_sources(tmp_path)
