"""Full declared KiCad coverage through the existing Catalog and HTTP boundary."""
import json
import shutil
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from embedded_copilot.api.project_catalog_models import EDAImportPreviewView
from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.engineering_eda.kicad_project_coverage_models import (
    ProjectCoverageV1,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogRuntime,
)
from tests.project_catalog.test_eda_import_runtime import _project

ORIGINAL = Path(__file__).resolve().parents[2] / 'acceptance/s06-third-party/kicad-pic-programmer-8.0.9/original'
ENTRY = 'native/pic_programmer.kicad_pro'
OWNER = {'X-Copilot-Requester':'owner-1', 'X-Environment-Tenant':'tenant-1'}

def _setup(tmp_path):
    service, project, source = _project(tmp_path)
    (source/'board.eprj3').unlink()
    shutil.copytree(ORIGINAL,source,dirs_exist_ok=True)
    app=FastAPI(); app.state.project_catalog_runtime=service; app.include_router(router)
    return service,project,source,app

def _input(head=None):
    return {'backend':'KICAD','native_entry_relative_path':ENTRY,'selected_native_entries':[],
        'expected_head_revision_id': None if head is None else head['import_revision_id'],
        'expected_head_revision_fingerprint': None if head is None else head['import_revision_fingerprint']}

def _source_hashes(source):
    return {p.relative_to(source).as_posix():sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}

def test_full_coverage_api_store_restart_and_file_currentness(tmp_path,monkeypatch):
    service,project,source,app=_setup(tmp_path)
    before=_source_hashes(source)
    path=f'/api/v1/copilot/projects/{project}'
    with TestClient(app) as client:
        preview=client.post(path+'/eda-import-previews',headers=OWNER,json=_input())
        assert preview.status_code==200,preview.text[:500]
        p=preview.json(); c=p['project_coverage']
        assert len(c['files'])==11 and c['counts']['objects_total']==45396
        assert c['coverage_state']=='PARTIAL' and c['dependency_state']=='EXTERNAL_UNVERIFIED'
        assert c['counts']['unknown_total']==2 and c['counts']['recognized_total']==45394
        assert {u['reason'] for u in c['unknowns']}=={'OPAQUE_GEOMETRY'}
        assert c['objects'][0]['source_span'] is None
        assert p['verified'] is p['can_modify'] is False
        assert _source_hashes(source)==before
        model=ProjectCoverageV1.model_validate_json(json.dumps(c))
        assert model.fingerprint==c['fingerprint'] and len(model.objects)==45396
        committed=client.post(path+'/eda-imports',headers=OWNER,json={'preview_id':p['preview_id'],'preview_fingerprint':p['preview_fingerprint']})
        assert committed.status_code==200,committed.text[:500]
        revision=committed.json()
        assert revision['project_coverage']==c
        assert revision['source_currentness']==revision['observation_currentness']=='CURRENT'
        assert revision['verified'] is revision['can_modify'] is False
        url=path+'/eda-imports/'+revision['import_revision_id']
        get=client.get(url,headers=OWNER,params={'fingerprint':revision['import_revision_fingerprint']})
        assert get.status_code==200 and get.json()['project_coverage']==c
    with sqlite3.connect(tmp_path/'catalog.sqlite3') as conn:
        raw=conn.execute('SELECT body FROM project_eda_import_revisions').fetchone()[0]
        stored=json.loads(raw)
        assert stored['project_coverage']==c
        canonical=json.dumps(c,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        assert 30_000_000<len(canonical)<32*1024*1024
    args=tmp_path/'restart.json'; args.write_text(json.dumps({'root':str(tmp_path),'project':project,
        'revision':revision['import_revision_id'],'fingerprint':revision['import_revision_fingerprint']}),encoding='utf8')
    script='''
import json,sys,hashlib
from pathlib import Path
from tests.s02_offline_guard import pytest_configure,pytest_unconfigure
pytest_configure()
from fastapi import FastAPI
from fastapi.testclient import TestClient
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.api.project_catalog_routes import router
a=json.loads(Path(sys.argv[1]).read_text(encoding='utf8')); root=Path(a['root'])
r=ProjectCatalogRuntime(project_root=root/'projects',state_database=root/'catalog.sqlite3',initialize_async=False)
app=FastAPI();app.state.project_catalog_runtime=r;app.include_router(router)
with TestClient(app) as client:
    path='/api/v1/copilot/projects/'+a['project']+'/eda-imports'
    headers={'X-Copilot-Requester':'owner-1','X-Environment-Tenant':'tenant-1'}
    listed=client.get(path,headers=headers,params={'backend':'KICAD','native_entry_relative_path':'native/pic_programmer.kicad_pro'})
    item=listed.json()['items'][0]
    response=client.get(path+'/'+item['import_revision_id'],headers=headers,params={'fingerprint':item['import_revision_fingerprint']})
    assert response.status_code==200,response.text[:500]
    v=response.json();c=v['project_coverage']
    print(json.dumps({'coverage_sha':hashlib.sha256(json.dumps(c,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'objects':len(c['objects']),'currentness':v['observation_currentness'],'revision':item['import_revision_id']}))
pytest_unconfigure()
'''
    child=subprocess.run([sys.executable,'-c',script,str(args)],capture_output=True,text=True,encoding='utf8',timeout=90,check=False)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout.splitlines()[-1])=={'coverage_sha':sha256(canonical).hexdigest(),'objects':45396,
        'currentness':'CURRENT','revision':revision['import_revision_id']}
    def read():
        return service.eda_import_revision(project,revision['import_revision_id'],revision['import_revision_fingerprint'],tenant_id='tenant-1',requester='owner-1')
    extra=source/'extra.txt';extra.write_bytes(b'new');assert read()['source_currentness']=='STALE';extra.unlink()
    member=source/'libs/pic_programmer_fp.pretty/RV2X4.kicad_mod';original=member.read_bytes()
    member.unlink();assert read()['source_currentness']=='STALE';member.write_bytes(original)
    renamed=member.with_name('renamed.kicad_mod');member.rename(renamed);assert read()['source_currentness']=='STALE';renamed.rename(member)
    member.write_bytes(original+b'\n');assert read()['source_currentness']=='STALE';member.write_bytes(original)
    assert read()['source_currentness']=='CURRENT' and _source_hashes(source)==before
    # Parser semantic drift leaves byte currentness intact, but invalidates the observation.
    from embedded_copilot.engineering_eda import kicad_project_coverage as parser
    monkeypatch.setitem(parser._SEXPR_CHILDREN, 'kicad_pcb', parser._SEXPR_CHILDREN['kicad_pcb'] - {'paper'})
    drifted=read()
    assert drifted['source_currentness']=='CURRENT' and drifted['observation_currentness']=='STALE'
    assert drifted['project_coverage']==c and drifted['import_revision_fingerprint']==revision['import_revision_fingerprint']
    assert _source_hashes(source)==before

def test_coverage_owner_cas_unknown_and_manifest_validation(tmp_path,monkeypatch):
    service,project,source,_app=_setup(tmp_path)
    def preview():
        return service.create_eda_import_preview(project,tenant_id='tenant-1',requester='owner-1',backend='KICAD',
            native_entry_relative_path=ENTRY,selected_native_entries=(),expected_head_revision_id=None,expected_head_revision_fingerprint=None)
    scan=service._artifact_intelligence.scan_eda_import_directory
    calls=[]
    def blocked(*args,**kwargs):calls.append(True);raise AssertionError('owner bypass')
    monkeypatch.setattr(service._artifact_intelligence,'scan_eda_import_directory',blocked)
    with pytest.raises(ProjectCatalogForbidden):
        service.create_eda_import_preview(project,tenant_id='tenant-1',requester='intruder',backend='KICAD',
            native_entry_relative_path=ENTRY,selected_native_entries=(),expected_head_revision_id=None,expected_head_revision_fingerprint=None)
    assert calls==[]
    monkeypatch.setattr(service._artifact_intelligence,'scan_eda_import_directory',scan)
    first=preview();second=preview()
    head=service.commit_eda_import_preview(project,first['preview_id'],first['preview_fingerprint'],tenant_id='tenant-1',requester='owner-1')
    with pytest.raises(ProjectCatalogConflict,match='HEAD'):
        service.commit_eda_import_preview(project,second['preview_id'],second['preview_fingerprint'],tenant_id='tenant-1',requester='owner-1')
    raw=service._repository.eda_import_preview(project,first['preview_id'],tenant_id='tenant-1',requester='owner-1')['revision']
    bad=json.loads(json.dumps(raw));bad['project_coverage']['extra']='forbidden'
    with pytest.raises(ValidationError):service._repository._eda_coverage(bad)
    bad=json.loads(json.dumps(raw));bad['manifest'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='MANIFEST'):service._repository._eda_coverage(bad)
    pcb=source/'pic_programmer.kicad_pcb';pcb.write_bytes(pcb.read_bytes().replace(b'(kicad_pcb',b'(kicad_pcb (future_dependency "external")',1))
    new=service.create_eda_import_preview(project,tenant_id='tenant-1',requester='owner-1',backend='KICAD',
        native_entry_relative_path=ENTRY,selected_native_entries=(),expected_head_revision_id=head['import_revision_id'],expected_head_revision_fingerprint=head['import_revision_fingerprint'])
    assert new['project_coverage']['dependency_state']=='UNKNOWN'
    assert new['project_coverage']['coverage_state']=='PARTIAL'
    assert any(u['reason']=='UNKNOWN_KEYWORD' for u in new['project_coverage']['unknowns'])

def test_legacy_unmatched_profile_omits_coverage(tmp_path):
    service,project,_source=_project(tmp_path)
    preview=service.create_eda_import_preview(project,tenant_id='tenant-1',requester='owner-1',backend='JLCEDA_PRO',
        native_entry_relative_path='native/board.eprj3',selected_native_entries=(),expected_head_revision_id=None,expected_head_revision_fingerprint=None)
    assert 'project_coverage' not in preview
    assert 'project_coverage' not in EDAImportPreviewView.model_validate(preview).model_dump(mode='json')
    committed=service.commit_eda_import_preview(project,preview['preview_id'],preview['preview_fingerprint'],tenant_id='tenant-1',requester='owner-1')
    assert 'project_coverage' not in committed
    prior=committed['import_revision_fingerprint']
    restarted=ProjectCatalogRuntime(project_root=tmp_path/'projects',state_database=tmp_path/'catalog.sqlite3',initialize_async=False)
    assert restarted.eda_import_revision(project,committed['import_revision_id'],prior,tenant_id='tenant-1',requester='owner-1')['import_revision_fingerprint']==prior
