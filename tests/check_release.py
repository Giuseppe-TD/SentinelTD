"""Dependency-free checks for a clean source release, not an integration test."""
import ast
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
issues = []
python_count = 0
for file in root.rglob('*'):
    if not file.is_file() or any(part in ('.git', 'node_modules', '__pycache__', '.language-backups') for part in file.relative_to(root).parts):
        continue
    rel = file.relative_to(root)
    if re.search(r'\.(db(?:-\w+)?|sqlite\w*|dump|bak|log)$', file.name, re.I):
        issues.append(f'Runtime data in release: {rel}')
    if file.name == '.env' or file.name == 'bck.env':
        issues.append(f'Private environment file in release: {rel}')
    if file.suffix == '.py':
        ast.parse(file.read_text('utf-8'), filename=str(rel))
        python_count += 1
    if file.name in ('.env.example', '.env.template'):
        for line in file.read_text('utf-8').splitlines():
            match = re.match(r'(\w+)=(.*)', line)
            if match and re.search(r'(PASSWORD|PASS|SECRET|TOKEN$|KEY$|^USERS$)', match[1]) and match[2]:
                issues.append(f'Example credential is not empty: {match[1]}')
if issues:
    raise SystemExit('\n'.join(issues))
print(f'Clean release checks passed; parsed {python_count} Python files.')
