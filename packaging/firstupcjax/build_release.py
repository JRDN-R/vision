"""Build the private PC setup and separate shareable plugin from this source tree."""
import argparse,hashlib,json,re,shutil,subprocess,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
NODE_SHA='158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541'
NAME='FirstUPCJax-Admins-FUPCJ-1.0.2'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def archive(source,target,prefix):
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(source.rglob('*')):
            if p.is_file():z.write(p,prefix+'/'+p.relative_to(source).as_posix())
def validate_plugin(path):
    with zipfile.ZipFile(path) as z:
        prefix='firstupcjax-admins-fupcj/'
        manifest=json.loads(z.read(prefix+'plugin.json'));json.loads(z.read(prefix+'mcp.json'))
        for key in ('logo','composerIcon'):
            relative=manifest['extensions']['com.openai']['interface'][key].removeprefix('./');assert z.read(prefix+relative)
        assert not any(b'pco_pat_' in z.read(name) for name in z.namelist())
        assert len(manifest['extensions']['com.openai']['interface']['shortDescription'])<=30

def build(args):
    private=json.loads(args.private_config.read_text(encoding='utf-8-sig'))
    assert re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',private['ownerEmail'])
    assert private['backendUrl']=='https://desktop-vjt2br2.tail385c9d.ts.net'
    assert all(isinstance(private['pco'].get(k),str) and private['pco'][k] for k in ('client_id','secret'))
    assert sha(args.node_archive)==NODE_SHA,'Node archive checksum did not match'
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='FirstUPCJax Sound Room ') as work:
        work=Path(work);stage=work/NAME;stage.mkdir()
        for name in ('README-FIRST.md','INSTALL-FIRSTUPCJAX-ADMINS.cmd','Sign-In-Planning-Center.cmd'):
            shutil.copy2(HERE/name,stage/name)
        shutil.copytree(HERE/'installer',stage/'installer')
        (stage/'private').mkdir();(stage/'private/pc-config.json').write_text(json.dumps(private,indent=2)+'\n')
        (stage/'vendor').mkdir();shutil.copy2(args.node_archive,stage/'vendor'/args.node_archive.name)
        source=stage/'source';source.mkdir()
        for name in ('Vision.html','index.html','README.md','LICENSE','vortex_menu_icon.png','logo or node.PNG'):
            item=ROOT/name
            if item.is_file():
                if private['pco']['secret'].encode() in item.read_bytes():raise RuntimeError('Private credential found in root source.')
                shutil.copy2(item,source/name)
        # Git tracked and new source only; never include logs, credentials or dependencies.
        listing=(subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
                 if (ROOT/'.git').exists() else json.loads((ROOT/'SOURCE-FILES.json').read_text(encoding='utf-8')))
        (source/'SOURCE-FILES.json').write_text(json.dumps(listing,indent=2),encoding='utf-8')
        for rel in listing:
            if not rel or not any(rel.startswith(p+'/') for p in ('vision-pc','web','tests','admins','packaging/firstupcjax','.github/workflows')):continue
            p=ROOT/rel
            if not p.is_file() or p.is_symlink() or '__pycache__' in p.parts:continue
            data=p.read_bytes()
            if private['pco']['secret'].encode() in data or private['pco']['client_id'].encode() in data:raise RuntimeError('Private credential found in source; package not created.')
            dest=source/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        plugin=args.output/'FirstUPCJax-Admins-Plugin.zip';archive(HERE/'plugin',plugin,'firstupcjax-admins-fupcj');validate_plugin(plugin)
        shutil.copy2(plugin,stage/plugin.name)
        if args.report:shutil.copy2(args.report,stage/'VALIDATION.txt')
        files=sorted(p for p in stage.rglob('*') if p.is_file())
        (stage/'checksums.sha256').write_text(''.join(sha(p)+'  '+p.relative_to(stage).as_posix()+'\n' for p in files))
        target=args.output/(NAME+'.zip');archive(stage,target,NAME)
        clean=work/'clean extract';clean.mkdir()
        with zipfile.ZipFile(target) as z:
            assert z.testzip() is None;z.extractall(clean)
        unpacked=clean/NAME
        for line in (unpacked/'checksums.sha256').read_text().splitlines():
            checksum,name=line.split('  ',1);assert sha(unpacked/name)==checksum
        assert (unpacked/'installer/Install-FirstUPCJax-Admins.ps1').is_file()
        saved=json.loads((unpacked/'private/pc-config.json').read_text());assert saved==private
        validate_plugin(unpacked/plugin.name)
        # Real source tests from this exact archive, including native runtime/catalog.
        subprocess.run([args.python,'-m','pytest','tests/test_church_admin.py','tests/test_church_oauth.py','tests/test_church_package.py','tests/test_church_browser_install.py','-q'],cwd=unpacked/'source',check=True)
        subprocess.run(['node','--test','tests/church-scheduler.test.mjs'],cwd=unpacked/'source',check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['node','--check','web/admins.js'],cwd=unpacked/'source',check=True)
        subprocess.run([args.python,'web/build.py'],cwd=unpacked/'source',check=True)
        output=args.output/'SHA256SUMS.txt';output.write_text(''.join(sha(p)+'  '+p.name+'\n' for p in (target,plugin)))
        print('Release built, re-extracted, checksums/credentials validated, and extracted tests passed.')
        print(target);print('SHA-256:',sha(target))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--private-config',type=Path,required=True);parser.add_argument('--node-archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--python',default=__import__('sys').executable);parser.add_argument('--report',type=Path)
    build(parser.parse_args())
