"""Automatic part of docs/GUARDRAILS.md. Standard library only. Exit code 0 = pass."""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_-]{10,}|apify_api_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|TABPFN_TOKEN\s*=\s*\S{8,})")
BRAIN_FORBIDDEN = re.compile(r"\b(?:ground_truth|payment_failure|stockout_surge|search_regression|ad_budget_exhausted)\b")
ANSWER_ROUTE = re.compile(r"/api/(?:state|inject|end|db|ground_truth)(?=[/?#\s\"']|$)")


def git(*args, data=None):
    return subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True, check=True).stdout


def tracked():
    return [p.decode('utf-8', 'surrogateescape') for p in git('ls-files', '-z').split(b'\0') if p]


def forbidden_path(rel):
    path = Path(rel)
    return (any(p == '.env' or p.startswith('.env.') for p in path.parts)
            or rel.startswith(('data/', 'incidents/')))


def decode(data):
    # BOM-aware UTF-16/32 as well as ordinary UTF-8; binary files are not text.
    if data.startswith((b'\xff\xfe\0\0', b'\0\0\xfe\xff')):
        encoding = 'utf-32'
    elif data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    elif b'\0' in data:
        # UTF-16 without a BOM is common in files produced on Windows.
        encoding = 'utf-16-le' if data[1::2].count(0) > len(data) // 4 else 'utf-16-be'
    else:
        encoding = 'utf-8-sig'
    try:
        return data.decode(encoding)
    except UnicodeError:
        return ''


def brain_checks(rel, text):
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if BRAIN_FORBIDDEN.search(line) or ANSWER_ROUTE.search(line):
            problems.append(f'{rel}:{n}: brain/ must not read answers (rule 2)')
    if not rel.endswith('.py'):
        return problems
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return problems + [f'{rel}: cannot parse Python for boundary checks']
    sqlite_modules, connects = {'sqlite3'}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
            sqlite_modules.update(a.asname or a.name for a in node.names if a.name == 'sqlite3')
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or '']
            if node.module == 'sqlite3':
                connects.update(a.asname or a.name for a in node.names if a.name == 'connect')
            if node.level:
                modules.extend(a.name for a in node.names)
        else:
            continue
        if any(m.split('.')[0] in {'sim', 'ui'} for m in modules):
            problems.append(f'{rel}:{node.lineno}: brain/ must not import sim/ or ui/ (rule 2)')
    if rel != 'brain/data.py':
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if ((isinstance(f, ast.Name) and f.id in connects)
                    or (isinstance(f, ast.Attribute) and f.attr == 'connect'
                        and isinstance(f.value, ast.Name) and f.value.id in sqlite_modules)):
                problems.append(f'{rel}:{node.lineno}: SQLite access must go through brain/data.py')
    return problems


def history_problems():
    problems = []
    # Scan reachable objects once, including deleted files and other local refs.
    # Object enumeration gives only one name per blob; inspect changed paths
    # separately so a forbidden path sharing an ordinary file's blob is caught.
    names = git('log', '--all', '--format=', '--name-only', '-z').split(b'\0')
    if any(forbidden_path(name.decode('utf-8', 'surrogateescape').lstrip('\n'))
           for name in names if name):
        problems.append('history: forbidden tracked path (rule 1 / contracts)')
    objects = git('rev-list', '--objects', '--all').splitlines()
    if not objects:
        return problems
    ids = [line.split(b' ', 1)[0] for line in objects]
    batch = git('cat-file', '--batch', data=b'\n'.join(ids) + b'\n')
    offset = 0
    for entry in objects:
        end = batch.index(b'\n', offset)
        oid, kind, size = batch[offset:end].split()
        start = end + 1
        payload = batch[start:start + int(size)]
        offset = start + int(size) + 1
        if kind not in {b'blob', b'commit'}:
            continue
        label = oid.decode()[:12]
        if kind == b'blob':
            rel = entry.partition(b' ')[2].decode('utf-8', 'surrogateescape')
            if forbidden_path(rel):
                problems.append(f'history object {label}: forbidden tracked path (rule 1 / contracts)')
        if SECRET.search(decode(payload)):
            problems.append(f'history object {label}: looks like a secret (rule 1)')
    return problems


def main():
    try:
        problems = []
        for rel in tracked():
            if forbidden_path(rel):
                problems.append(f'{rel}: must not be tracked (rule 1 / contracts)')
            path = ROOT / rel
            if not path.is_file():
                continue
            text = decode(path.read_bytes())
            for n, line in enumerate(text.splitlines(), 1):
                if SECRET.search(line):
                    problems.append(f'{rel}:{n}: looks like a secret (rule 1)')
            if rel.startswith('brain/'):
                problems.extend(brain_checks(rel, text))
        problems.extend(history_problems())
    except (OSError, subprocess.CalledProcessError, ValueError):
        print('Guardrail check FAILED: unable to inspect repository.')
        return 1
    if problems:
        print('Guardrail check FAILED:')
        for problem in problems:
            print(' -', problem)
        return 1
    print('Guardrail check passed (working tree, reachable history, brain boundaries).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
