"""Temporary integration construction; never updates remote refs or releases.

Only immutable Git objects are uploaded after matching the locally tested tree.
The construction controls are excluded from the final product tree.
"""
from pathlib import Path
from zipfile import ZipFile
import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request

REPOSITORY = 'knowco2-project/knowco2-firmware'
EXPECTED_TREE = '735d57610830007ea3a0d9bd242b7439d70a2547'
INPUTS = {
    'main': 'aa7533ca40be5fa352a77f2f550dfbd0dfdd2e7e',
    'pr22': '93f2ed67fb19aecde60db30a9f7cff822c599b4c',
    'pr23': 'edfe344f7678822ae809429bfce8edb0008419ce',
    'pr24': 'e0691b809d55c2dced8d9906525035f9cddd5e3f',
    'pr25': '6b8826b430752c23ce4d3888d157a34cf290b606',
}
OVERLAYS = ('.github/CODEOWNERS', 'docs/releases/1.0.0-gates.json',
    'docs/releases/1.0.0-readiness.md', 'tools/release_gate.py',
    'tests/test_release_gate.py', 'tests/test_ota_preservation.py')
SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ['RUNNER_TEMP']) / 'knowco2-integration-objects'
RESULT = SOURCE / 'integration-object-result'


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def guard():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
        raise RuntimeError('Wrong repository')
    if os.environ.get('GITHUB_REF') != 'refs/heads/integration/firmware-1.0.0':
        raise RuntimeError('Wrong integration branch')
    if not os.environ.get('CODEBUILD_BUILD_ID'):
        raise RuntimeError('CodeBuild required')


def prepare():
    guard()
    if ROOT.exists():
        raise RuntimeError('Refusing to overwrite an existing construction directory')
    ROOT.mkdir()
    for name, sha in INPUTS.items():
        if git(SOURCE, 'rev-parse', sha+'^{commit}').decode().strip() != sha:
            raise RuntimeError('Pinned commit missing: '+name)
        target = ROOT/name
        with ZipFile(io.BytesIO(git(SOURCE, 'archive', '--format=zip', sha))) as archive:
            for info in archive.infolist():
                relative = Path(info.filename)
                if relative.is_absolute() or '..' in relative.parts:
                    raise RuntimeError('Unsafe archive path')
                archive.extract(info, target)
                path = target/relative
                if path.is_file():
                    path.chmod((info.external_attr >> 16) & 0o777 or 0o644)
    base, work = ROOT/'main', ROOT/'combined'
    shutil.copytree(base, work)
    for name in ('pr22', 'pr23', 'pr24', 'pr25'):
        incoming = ROOT/name
        paths = {p.relative_to(base) for p in base.rglob('*') if p.is_file()}
        paths |= {p.relative_to(incoming) for p in incoming.rglob('*') if p.is_file()}
        for relative in sorted(paths):
            b, t, o = base/relative, incoming/relative, work/relative
            bd = b.read_bytes() if b.exists() else None
            td = t.read_bytes() if t.exists() else None
            od = o.read_bytes() if o.exists() else None
            if bd == td:
                continue
            if od == bd or od == td:
                if td is None:
                    o.unlink()
                else:
                    o.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(t, o)
            elif bd is None and td is not None and od is not None and str(relative) == 'tests/fixtures/README.md':
                o.write_bytes(od+b'\n\n'+td)
            elif bd is not None and td is not None and od is not None and b'\x00' not in bd+td+od:
                result = subprocess.run(['git', 'merge-file', '-p', '-L', 'current', '-L', 'base', '-L', 'incoming', str(o), str(b), str(t)], stdout=subprocess.PIPE)
                if result.returncode not in (0, 1):
                    raise RuntimeError('Three-way merge failed: '+str(relative))
                if result.returncode == 1 and str(relative) != '.github/workflows/ci.yml':
                    raise RuntimeError('Unreviewed conflict: '+str(relative))
                o.write_bytes(result.stdout)
            else:
                raise RuntimeError('Unreviewed merge shape: '+str(relative))
    environment = dict(os.environ)
    environment.pop('GITHUB_TOKEN', None)
    environment.pop('GH_TOKEN', None)
    environment['KNOWCO2_INTEGRATION_ROOT'] = str(work)
    subprocess.run([sys.executable, str(SOURCE/'.integration-transfer/harden.py')], env=environment, check=True)
    for relative in OVERLAYS:
        destination = work/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE/relative, destination)
    git(work, 'init', '-q')
    # Read the original immutable objects for comparison, without copying tokens
    # or altering any original repository ref.
    (work/'.git/objects/info/alternates').write_text(str(SOURCE/'.git/objects')+'\n')
    git(work, 'add', '.')
    actual = git(work, 'write-tree').decode().strip()
    RESULT.mkdir(exist_ok=True)
    (RESULT/'actual-tree.txt').write_text(actual+'\n')
    (RESULT/'file-objects.txt').write_bytes(git(work, 'ls-files', '-s'))
    print('Constructed tree:', actual)
    if actual != EXPECTED_TREE:
        raise RuntimeError('Tree differs from locally tested candidate; no Git objects uploaded')
    git(work, 'diff', '--cached', '--check', INPUTS['main'])
    (RESULT/'verified-tree.json').write_text(json.dumps({'tree': actual, 'inputs': INPUTS, 'control_commit': os.environ['GITHUB_SHA'], 'ref_updated': False}, indent=2)+'\n')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Refusing Git API redirect')


def upload():
    guard()
    work = ROOT/'combined'
    if git(work, 'write-tree').decode().strip() != EXPECTED_TREE:
        raise RuntimeError('Tree mismatch before upload')
    token = os.environ['GITHUB_TOKEN']
    opener = urllib.request.build_opener(NoRedirect())
    seen = set()
    def post(kind, payload, expected):
        if kind not in ('blobs', 'trees'):
            raise RuntimeError('Only immutable Git objects may be created')
        request = urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+'/git/'+kind,
            data=json.dumps(payload).encode(), method='POST', headers={
                'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'})
        with opener.open(request, timeout=30) as response:
            result=json.load(response)
        if result.get('sha') != expected:
            raise RuntimeError('Uploaded Git object hash mismatch')
    def ensure(sha, kind):
        if sha in seen:
            return
        exists = subprocess.run(['git', '-C', str(SOURCE), 'cat-file', '-e', sha], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if exists:
            seen.add(sha)
            return
        if kind == 'blob':
            raw = git(work, 'cat-file', 'blob', sha)
            post('blobs', {'content': base64.b64encode(raw).decode(), 'encoding': 'base64'}, sha)
        elif kind == 'tree':
            entries=[]
            for line in git(work, 'ls-tree', '-z', sha).split(b'\0'):
                if not line:
                    continue
                metadata, path = line.split(b'\t', 1)
                mode, child_kind, child_sha = metadata.decode().split()
                ensure(child_sha, child_kind)
                entries.append({'path': path.decode(), 'mode': mode, 'type': child_kind, 'sha': child_sha})
            post('trees', {'tree': entries}, sha)
        else:
            raise RuntimeError('Unexpected Git object type')
        seen.add(sha)
    ensure(EXPECTED_TREE, 'tree')
    (RESULT/'uploaded-tree.txt').write_text(EXPECTED_TREE+'\n')
    print('Verified immutable tree uploaded; NO branch, tag, release or deployment modified:', EXPECTED_TREE)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare','upload'])
    args=parser.parse_args()
    prepare() if args.mode == 'prepare' else upload()
