import socket
from pathlib import Path

import pytest
import yaml

from scripts import setup_handoff_demo as setup


def test_compose_rejects_extra_services_or_external_mounts(tmp_path):
    original = Path(__file__).resolve().parents[1] / 'compose.handoff.yaml'
    setup.validate_compose(original)
    config = yaml.safe_load(original.read_text(encoding='utf-8'))
    config['services']['db']['volumes'].append('another_volume:/unsafe')
    changed = tmp_path / 'compose.yaml'
    changed.write_text(yaml.safe_dump(config), encoding='utf-8')
    with pytest.raises(ValueError, match='Unexpected'):
        setup.validate_compose(changed)


def test_port_validation_rejects_bad_or_busy_ports():
    with pytest.raises(ValueError):
        setup.validate_ports(55434, 55434)
    with pytest.raises(ValueError):
        setup.validate_ports(0, 55435)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        occupied = listener.getsockname()[1]
        other = 55435 if occupied != 55435 else 55434
        with pytest.raises(OSError):
            setup.validate_ports(occupied, other)


def test_existing_environment_is_never_overwritten(tmp_path, monkeypatch):
    environment = tmp_path / '.env'; environment.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(setup, 'ENV_FILE', environment)
    monkeypatch.setattr(setup, 'LOG', tmp_path / '.handoff-setup.log')
    with pytest.raises(FileExistsError, match='Existing'):
        setup.setup(55434, 55435)
    assert environment.read_text(encoding='utf-8') == 'keep'
    assert not (tmp_path / '.handoff-setup.log').exists()


def test_project_guard_rejects_existing_project_names_before_docker():
    with pytest.raises(ValueError):
        setup.project_is_free('master_project')
