"""Build dist/layer-tools-<version>.zip: one layer-tools/ folder holding the
plugin files, ready to unzip into a Designer project's plugins folder.

    python tools/build_release.py

Checks that the version is the same in all six places before building.
"""
import io
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = 'layer-tools'
FILES = ['d3plugin.json', 'index.html', '__init__.py', 'icon.svg', 'icon.png', 'README.md', 'CHANGELOG.md',
         'VERSION.txt', 'LICENSE', 'docs/screenshot.png']


def read(name):
    with io.open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def versions():
    html = read('index.html')
    return {
        'VERSION.txt': read('VERSION.txt').strip(),
        '__init__.py': re.search(r"^VERSION = '([^']+)'", read('__init__.py'), re.M).group(1),
        'd3plugin.json': json.loads(read('d3plugin.json'))['name'].rsplit(' v', 1)[-1],
        'index.html <title>': re.search(r'<title>Layer Tools v([^<]+)</title>', html).group(1),
        'index.html badge': re.search(r'id="ver">v([^<]+)<', html).group(1),
        'index.html VERSION': re.search(r"var VERSION = '([^']+)'", html).group(1),
    }


def main():
    v = versions()
    if len(set(v.values())) != 1:
        sys.exit('version mismatch:\n' + '\n'.join('  %-20s %s' % kv for kv in sorted(v.items())))
    version = v['VERSION.txt']
    out = os.path.join(ROOT, 'dist', '%s-%s.zip' % (FOLDER, version))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(os.path.join(ROOT, name), '%s/%s' % (FOLDER, name))
    print('built %s (%d files, v%s)' % (os.path.relpath(out, ROOT), len(FILES), version))


if __name__ == '__main__':
    main()
