from pathlib import Path
import json,re,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'packaging/firstupcjax'

def test_portable_plugin_branded_and_auth_requires_oauth():
    plugin=json.loads((PACKAGE/'plugin/plugin.json').read_text());mcp=json.loads((PACKAGE/'plugin/mcp.json').read_text())
    assert plugin['$schema']=='https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
    ui=plugin['extensions']['com.openai']['interface'];assert len(ui['shortDescription'])<=30
    for key in ('logo','composerIcon'):
        svg=ET.parse(PACKAGE/'plugin'/ui[key]).getroot();assert svg.attrib['width']==svg.attrib['height']=='520'
    server=mcp['mcpServers']['firstupcjax-admins'];assert server['url'].endswith('/firstupcjax/mcp')
    auth=server['extensions']['com.openai']['auth'];assert auth['type']=='oauth' and auth['client']=={'mode':'dcr'}
    for path in (PACKAGE/'plugin').rglob('*'):
        if path.is_file():assert 'pco_pat_' not in path.read_text()

def test_new_native_files_are_preserved_by_updater_inventory():
    text=(ROOT/'vision-pc/Setup-Vision-PC.ps1').read_text();items=re.search(r'\$ProcessorFiles = @\((.*?)\)',text).group(1)
    for file in (ROOT/'vision-pc').glob('church*'):
        if file.is_file():assert "'"+file.name+"'" in items
    assert 'SourceDirectory' in text

def test_zip_launcher_checks_nested_script_before_elevation():
    text=(PACKAGE/'INSTALL-FIRSTUPCJAX-ADMINS.cmd').read_text()
    assert text.index('if not exist')<text.index('-Verb RunAs')
    assert '%~dp0installer\\Install-FirstUPCJax-Admins.ps1' in text and 'Extract All' in text

def test_activation_health_checks_and_preserves_prior_config():
    text=(ROOT/'vision-pc/FirstUPCJax-Admins.ps1').read_text()
    assert 'nativeMcpReady' in text and 'credentialsSaved' in text
    assert '[IO.File]::WriteAllBytes($ConfigPath,$Previous)' in text
    assert "pco-credentials.dpapi" in text
    assert '-not (Test-Path -LiteralPath (Join-Path $ChurchData' in text
    assert '158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541' in text

def test_bridge_names_and_skill_match():
    text=(PACKAGE/'plugin/skills/church-admin/SKILL.md').read_text()
    assert text.startswith('---\nname: church-admin\ndescription:')
    assert 'send_scheduling_email' in text and 'notification_job' in text and 'needs_review' in text
    ui=(ROOT/'web/admins.js').read_text();assert '/oauth/' in ui and 'pairing' not in ui
    assert 'connect' in (ROOT/'web/auth.js').read_text()
