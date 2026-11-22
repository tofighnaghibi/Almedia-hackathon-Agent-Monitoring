"""Repository fixtures contain generated fake placeholders only."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_guardrails.py'
spec = importlib.util.spec_from_file_location('guardrails', SCRIPT)
guardrails = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guardrails)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', 'config', 'user.email', 'fixture@example.invalid'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test fixture'], cwd=tmp_path, check=True)
    target = tmp_path / 'scripts/check_guardrails.py'
    target.parent.mkdir()
    target.write_bytes(SCRIPT.read_bytes())
    return tmp_path


def write(repo, rel, text, encoding='utf-8'):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    subprocess.run(['git', 'add', '--', rel], cwd=repo, check=True)


def invoke(repo):
    return subprocess.run([sys.executable, str(repo / 'scripts/check_guardrails.py')],
                          cwd=repo.parent, capture_output=True, text=True)


@pytest.mark.parametrize('source', [
    'import sim', 'import ui.app as app', 'from sim import inject',
    'from ui.app import app', 'from . import sim',
    'import sqlite3 as db\ndb.connect("x")',
    'from sqlite3 import connect as db\ndb("x")',
    'import sqlite3\nsqlite3.connect("x")',
    'url = "http://localhost:8000/api/state"',
    'url = "/api/db/rows?table=x"',
    'url = "/api/inject"', 'url = "/api/end"',
    'query = "SELECT * FROM ground_truth"',
    'kind = "payment_failure"', 'kind = "stockout_surge"',
    'kind = "search_regression"', 'kind = "ad_budget_exhausted"',
])
def test_brain_boundaries(source):
    assert guardrails.brain_checks('brain/worker.py', source)


def test_allowed_brain_code_and_data_gateway():
    assert not guardrails.brain_checks('brain/worker.py',
        'payment_failure_rate = 0.1\npromo = True\nurl = "/api/graph"\n# simulation UI prose')
    assert not guardrails.brain_checks('brain/data.py', 'import sqlite3\nsqlite3.connect("x")')
    assert not guardrails.brain_checks('brain/worker.py', 'client.connect("x")')


@pytest.mark.parametrize('rel', ['.env', '.env.local', 'config/.env.production',
                                     'data/demo.csv', 'incidents/demo.json'])
def test_forbidden_paths_invocation(repo, rel):
    write(repo, rel, 'fake placeholder')
    result = invoke(repo)
    assert result.returncode == 1
    assert 'must not be tracked' in result.stdout


def test_clean_invocation(repo):
    write(repo, 'brain/worker.py', 'payment_failure_rate = 0.2\npromo = True')
    write(repo, 'README.md', 'No credentials here.')
    result = invoke(repo)
    assert result.returncode == 0, result.stdout
    assert 'passed' in result.stdout


def test_brain_violation_invocation(repo):
    write(repo, 'brain/worker.py', 'from sim import inject')
    result = invoke(repo)
    assert result.returncode == 1
    assert 'must not import' in result.stdout


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32'])
def test_encoded_fake_placeholder_redacted(repo, encoding):
    # Assemble a deliberately fake fixture so the test source is safe to scan.
    fake = 'TABPFN' + '_TOKEN=' + 'FAKE_PLACEHOLDER_FOR_TEST'
    write(repo, 'notes.txt', fake, encoding)
    result = invoke(repo)
    assert result.returncode == 1
    assert 'looks like a secret' in result.stdout
    assert fake not in result.stdout + result.stderr


def test_deleted_historical_fake_placeholder_redacted(repo):
    fake = 'TABPFN' + '_TOKEN=' + 'FAKE_PLACEHOLDER_FOR_TEST'
    write(repo, 'removed.txt', fake, 'utf-16')
    subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=repo, check=True)
    subprocess.run(['git', 'rm', '-q', 'removed.txt'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-qm', 'remove fixture'], cwd=repo, check=True)
    result = invoke(repo)
    assert result.returncode == 1
    assert 'history object' in result.stdout
    assert fake not in result.stdout + result.stderr


def test_commit_message_redacted(repo):
    write(repo, 'README.md', 'fixture')
    fake = 'TABPFN' + '_TOKEN=' + 'FAKE_PLACEHOLDER_FOR_TEST'
    subprocess.run(['git', 'commit', '-qm', fake], cwd=repo, check=True)
    result = invoke(repo)
    assert result.returncode == 1
    assert 'history object' in result.stdout
    assert fake not in result.stdout + result.stderr


def test_deleted_historical_env_variant(repo):
    write(repo, 'ordinary.txt', 'fake placeholder')
    write(repo, '.env.local', 'fake placeholder')
    subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=repo, check=True)
    subprocess.run(['git', 'rm', '-q', '.env.local'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-qm', 'remove fixture'], cwd=repo, check=True)
    result = invoke(repo)
    assert result.returncode == 1
    assert 'forbidden tracked path' in result.stdout


def test_git_failure_is_not_pass(tmp_path):
    target = tmp_path / 'scripts/check_guardrails.py'
    target.parent.mkdir()
    target.write_bytes(SCRIPT.read_bytes())
    result = invoke(tmp_path)
    assert result.returncode == 1
    assert 'unable to inspect' in result.stdout
