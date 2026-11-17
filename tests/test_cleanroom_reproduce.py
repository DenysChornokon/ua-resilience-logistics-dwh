"""Safety checks for the isolated reproduction setup."""
import copy

import pytest

from scripts.cleanroom_reproduce import safe_resources, strip_ports


@pytest.fixture
def compose():
    return {'services': {'db': {'image': 'postgres:17.11', 'environment': {
        'POSTGRES_DB': 'logistics_dwh', 'POSTGRES_USER': 'dwh_user',
        'POSTGRES_PASSWORD': '${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}'},
        'ports': ['127.0.0.1:55432:5432'],
        'volumes': ['postgres_data:/var/lib/postgresql/data', './sql:/sql:ro']}},
        'volumes': {'postgres_data': None}}


def test_strip_ports_keeps_only_expected_mounts(compose):
    result = strip_ports(copy.deepcopy(compose), 'db', 'postgres_data')
    assert 'ports' not in result['services']['db']
    assert result['services']['db']['volumes'] == compose['services']['db']['volumes']


@pytest.mark.parametrize('mutation', [
    lambda c: c['services']['db'].update({'privileged': True}),
    lambda c: c['services']['db']['volumes'].append('other:/data'),
    lambda c: c['volumes'].update({'postgres_data': {'external': True}}),
    lambda c: c['services'].update({'other': {}}),
])
def test_rejects_unexpected_compose_configuration(compose, mutation):
    mutation(compose)
    with pytest.raises(ValueError):
        strip_ports(compose, 'db', 'postgres_data')


def test_cleanup_resource_guard():
    project = 'dwhverify0123456789abcdef'
    safe_resources(project, [project + '_postgres_data'], [project + '_default'])
    with pytest.raises(ValueError):
        safe_resources(project, ['existing_project_data'], [])
    with pytest.raises(ValueError):
        safe_resources(project, [], ['existing_project_default'])
    with pytest.raises(ValueError):
        safe_resources('existing_project', [], [])
