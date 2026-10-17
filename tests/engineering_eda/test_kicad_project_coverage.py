"""Offline independent oracle and boundary checks for the finite source profile."""
import builtins
import io
import json
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_eda.kicad_project_coverage import (
    parse_kicad_project_coverage,
    validate_project_coverage_source,
)
from embedded_copilot.engineering_eda.kicad_project_coverage_models import (
    CoverageSourceFile,
    ProjectCoverageV1,
)

ROOT = Path(__file__).resolve().parents[2] / 'acceptance/s06-third-party/kicad-pic-programmer-8.0.9/original'
ENTRY = 'pic_programmer.kicad_pro'
PCB = 'pic_programmer.kicad_pcb'
SCH = 'pic_programmer.kicad_sch'
CHILD = 'pic_sockets.kicad_sch'
SOURCE_FP = 'sha256:' + 'a' * 64

@pytest.fixture(scope='module')
def payloads():
    # Only the named acceptance original root is read; no external libraries.
    result = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in ROOT.rglob('*') if p.is_file()}
    assert len(result) == 11
    return result


def manifest(payloads):
    return tuple(CoverageSourceFile(relative_path=p, byte_size=len(b), sha256=sha256(b).hexdigest()) for p,b in sorted(payloads.items()))


def parse(payloads):
    return parse_kicad_project_coverage(native_entry_relative_path=ENTRY,
        source_set_fingerprint=SOURCE_FP, payloads=payloads, expected_files=manifest(payloads))

@pytest.fixture(scope='module')
def coverage(payloads):
    return parse(payloads)


def independent_nodes(raw):
    """Test oracle: character scanner independent of the production tree parser.

    It retains only top-level head/atoms; nesting and quoted escapes determine
    boundaries. Expected counts below are fixed, never filled by new parser.
    """
    text = raw.decode('utf8'); stack=[]; result=[]; index=0
    while index < len(text):
        c = text[index]
        if c.isspace():
            index += 1; continue
        if c == '(':
            stack.append([]); index += 1; continue
        if c == ')':
            node=stack.pop()
            if len(stack) == 1:
                result.append(tuple(node))
            index += 1; continue
        if c == '"':
            index += 1; value=[]
            while index < len(text) and text[index] != '"':
                if text[index] == '\\':
                    index += 1
                value.append(text[index]); index += 1
            assert index < len(text)
            token=''.join(value); index += 1
        else:
            start=index
            while index < len(text) and not text[index].isspace() and text[index] not in '()':
                index += 1
            token=text[start:index]
        stack[-1].append(token)
    assert not stack
    return result


def test_fixed_original_oracle_counts(payloads, coverage):
    expected_pcb={'footprint':63,'net':112,'segment':370,'via':6,'zone':1,'gr_line':5,'gr_text':19}
    expected_sch={'symbol':105,'sheet':1,'wire':131,'junction':26,'no_connect':6,'label':10,'polyline':8,'text':8}
    for path, expected in ((PCB,expected_pcb),(SCH,expected_sch),(CHILD,{'symbol':19})):
        raw_counts=Counter(n[0] for n in independent_nodes(payloads[path]))
        observed=Counter(o.keyword for o in coverage.objects if o.source_file==path and o.parent_locator in {'/kicad_pcb','/kicad_sch'})
        for kind, count in expected.items():
            assert raw_counts[kind] == count
            assert observed[kind] == count
    # Source pad headers are independently counted using the fixed KiCad grammar.
    raw_pad_counts=Counter(re.findall(rb'^\s*\(pad\s+"[^"]*"\s+(\S+)\s+(\S+)',payloads[PCB],re.MULTILINE))
    expected={(b'thru_hole',b'rect'):24,(b'thru_hole',b'oval'):152,(b'np_thru_hole',b'circle'):6,
              (b'thru_hole',b'circle'):59,(b'thru_hole',b'roundrect'):3,(b'np_thru_hole',b'oval'):1,(b'smd',b'custom'):2}
    assert raw_pad_counts==expected
    parsed_pads=[o for o in coverage.objects if o.source_file==PCB and o.native_kind=='PAD']
    assert len(parsed_pads)==247
    assert Counter((o.values[1].encode(),o.values[2].encode()) for o in parsed_pads)==expected


def test_status_conservation_opaque_and_model_per_locator(payloads, coverage):
    assert coverage.coverage_state=='PARTIAL'
    assert coverage.dependency_state=='EXTERNAL_UNVERIFIED'
    assert coverage.counts.objects_total==len(coverage.objects)
    assert coverage.counts.recognized_total+coverage.counts.unknown_total==coverage.counts.objects_total
    assert coverage.counts.unknown_total==len(coverage.unknowns)==2
    assert sum(c.count for c in coverage.counts.by_kind)==len(coverage.objects)
    assert {u.reason for u in coverage.unknowns}=={'OPAQUE_GEOMETRY'}
    assert {f.relative_path for f in coverage.files}==set(payloads)
    assert all(f.parse_status=='OPAQUE' for f in coverage.files if f.role=='MODEL_3D')
    deps=[d for d in coverage.dependencies if d.source_file==PCB and d.kind=='MODEL_3D']
    external=[d for d in deps if d.state=='EXTERNAL_UNVERIFIED']
    local=[d for d in deps if d.state=='RESOLVED_IN_SOURCE']
    assert len(deps)==56 and len(external)==54 and len(local)==2
    assert len({d.reference for d in external})==17
    assert len({d.source_locator for d in external})==54
    assert all(d.reference.startswith('${KIPRJMOD}/') for d in local)
    assert all(not i.blocks_complete for i in coverage.issues if i.code=='DEPENDENCY_EXTERNAL')
    # The exact contract has no editable/verified assertion or native executor.
    assert 'verified' not in ProjectCoverageV1.model_fields
    assert 'can_modify' not in ProjectCoverageV1.model_fields


def test_complete_original_structure_and_child_links(payloads, coverage):
    # Count all opening lists with an independent quote-aware scanner.
    for path, raw in payloads.items():
        if not (path.endswith(('.kicad_pcb','.kicad_sch','.kicad_sym','.kicad_mod','lib-table'))):
            continue
        text=raw.decode('utf8'); quoted=False; escaped=False; total=0
        for char in text:
            if escaped: escaped=False; continue
            if char=='\\' and quoted: escaped=True; continue
            if char=='"': quoted=not quoted; continue
            if char=='(' and not quoted: total+=1
        objects=[o for o in coverage.objects if o.source_file==path]
        assert len(objects)==total
        assert all(o.source_span is None for o in objects)
        index={o.locator:o for o in objects}
        for obj in objects:
            assert all(index[c].parent_locator==obj.locator for c in obj.child_locators)
    custom=[o for o in coverage.objects if o.source_file==PCB and o.native_kind=='PAD' and o.values[2]=='custom']
    assert len(custom)==2
    assert all(any('/primitives[' in child for child in o.child_locators) for o in custom)


def test_unknown_keyword_single_reason(payloads):
    changed=dict(payloads); changed[PCB]=payloads[PCB].rstrip()[:-1]+b' (future_feature "original lexical value" (alien 9)))'
    c=parse(changed)
    unknown=[o for o in c.objects if o.source_file==PCB and o.native_kind=='UNKNOWN']
    assert {o.keyword for o in unknown}=={'future_feature','alien'}
    assert next(o for o in unknown if o.keyword=='future_feature').values==('original lexical value',)
    assert len([u for u in c.unknowns if u.source_file==PCB])==2
    assert c.coverage_state=='PARTIAL'


@pytest.mark.parametrize('path',(PCB,SCH,'libs/pic_programmer_schlib.kicad_sym'))
def test_admitted_unsupported_member_version_keeps_whole_file(payloads,path):
    changed=dict(payloads); changed[path]=re.sub(rb'\(version\s+\d+\)',b'(version 20990101)',payloads[path],count=1)
    c=parse(changed)
    assert c.coverage_state=='UNKNOWN' and c.dependency_state=='UNKNOWN'
    objects=[o for o in c.objects if o.source_file==path]
    assert len(objects)==1 and objects[0].native_kind=='UNKNOWN'
    assert next(u for u in c.unknowns if u.source_file==path).reason=='UNKNOWN_FORMAT'


def test_parse_failure_not_empty_dependency_set(payloads):
    changed=dict(payloads); changed[SCH]=payloads[SCH]+b'('
    c=parse(changed)
    assert c.coverage_state=='UNKNOWN' and c.dependency_state=='UNKNOWN'
    assert [u.reason for u in c.unknowns if u.source_file==SCH]==['PARSE_FAILURE']


@pytest.mark.parametrize('mode',('direct_pcb','missing_entry','legacy_meta'))
def test_unmatched_profiles_omit(payloads, mode):
    changed=dict(payloads); entry=ENTRY
    if mode=='direct_pcb': entry=PCB
    elif mode=='missing_entry': del changed[SCH]
    else:
        project=json.loads(changed[ENTRY]); project['meta']['version']=2
        changed[ENTRY]=json.dumps(project).encode()
    assert parse_kicad_project_coverage(native_entry_relative_path=entry,
        source_set_fingerprint=SOURCE_FP,payloads=changed,expected_files=manifest(changed)) is None


def test_unknown_project_property_explicit(payloads):
    changed=dict(payloads); project=json.loads(changed[ENTRY]); project['new_unknown']={'unexpected':[1,'two']}
    changed[ENTRY]=json.dumps(project).encode()
    c=parse(changed)
    unknown=[o for o in c.objects if o.source_file==ENTRY and o.native_kind=='UNKNOWN']
    assert len(unknown)==4 and c.coverage_state=='PARTIAL'
    assert c.counts.unknown_total==6


def test_duplicate_native_uuid_rejected(payloads):
    changed=dict(payloads)
    ids=re.findall(rb'\(uuid "([0-9a-f-]+)"\)',changed[PCB])
    assert len(ids)>2
    changed[PCB]=changed[PCB].replace(ids[1],ids[0],1)
    with pytest.raises(ValidationError,match='COVERAGE_DUPLICATE_NATIVE_ID'):
        parse(changed)


@pytest.mark.parametrize('mode',('size','hash','removed','added'))
def test_manifest_drift_rejected(payloads,mode):
    changed=dict(payloads)
    if mode=='size': changed[PCB]+=b' '
    elif mode=='hash': changed[PCB]=changed[PCB].replace(b'pcbnew',b'pqbnew',1)
    elif mode=='removed': del changed[CHILD]
    else: changed['extra.txt']=b'extra'
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        parse_kicad_project_coverage(native_entry_relative_path=ENTRY,
            source_set_fingerprint=SOURCE_FP,payloads=changed,expected_files=manifest(payloads))


def test_missing_local_member_and_sheet_dependency(payloads):
    changed=dict(payloads); del changed['libs/pic_programmer_fp.pretty/RV2X4.kicad_mod']
    c=parse(changed)
    assert c.dependency_state=='MISSING'
    assert any(d.kind=='FOOTPRINT_LIBRARY' and d.reference=='footprints:RV2X4' and d.state=='MISSING' for d in c.dependencies)
    changed=dict(payloads); del changed[CHILD]
    c=parse(changed)
    assert c.dependency_state=='MISSING'
    assert any(d.kind=='SCHEMATIC_SHEET' and d.state=='MISSING' for d in c.dependencies)


def test_unsafe_model_reference_is_symbolic_and_unsupported(payloads):
    changed=dict(payloads); changed[PCB]=payloads[PCB].replace(b'${KIPRJMOD}/libs/3d_shapes/',b'${KIPRJMOD}/../outside/',1)
    c=parse(changed)
    assert c.dependency_state=='UNKNOWN' and c.coverage_state=='PARTIAL'
    assert any(d.state=='UNSUPPORTED' and '/..' in d.reference for d in c.dependencies)


def mutable_envelope(coverage):
    value=coverage.model_dump(mode='python')
    value['fingerprint']='sha256:'+'0'*64
    return value


@pytest.mark.parametrize('mode',('counts','child','relation','unknown','span','state','extra'))
def test_strict_envelope_rejects_forgery(coverage,mode):
    value=mutable_envelope(coverage)
    if mode=='counts': value['counts']['recognized_total']+=1
    elif mode=='child': value['objects'][0]['child_locators']+=('/invented',)
    elif mode=='relation': value['relations'][0]['target_locator']='/invented'
    elif mode=='unknown': value['unknowns']=value['unknowns'][:-1]
    elif mode=='span': value['objects'][0]['source_span']={'byte_start':2**30,'byte_length':1,'sha256':'a'*64}
    elif mode=='state': value['coverage_state']='COMPLETE_FOR_DECLARED_SOURCE_OBJECTS'
    else: value['can_modify']=True
    with pytest.raises(ValidationError): ProjectCoverageV1.model_validate(value)


def test_wrong_span_slice_hash_rejected(payloads,coverage):
    value=mutable_envelope(coverage)
    index=next(i for i,o in enumerate(coverage.objects) if o.source_file==PCB)
    value['objects'][index]['source_span']={'byte_start':0,'byte_length':1,'sha256':'a'*64}
    forged=ProjectCoverageV1.model_validate(value)
    with pytest.raises(ValueError,match='COVERAGE_SPAN_INVALID'):
        validate_project_coverage_source(forged,payloads,manifest(payloads))


def test_missing_net_relation_blocks_complete(payloads):
    changed=dict(payloads); changed[PCB]=re.sub(rb'\(net 1 "',b'(net 999999 "',payloads[PCB],count=1)
    c=parse(changed)
    assert any(i.code=='RELATION_UNRESOLVED' and i.blocks_complete for i in c.issues)
    assert c.coverage_state=='PARTIAL'


def test_parser_no_filesystem_reads_and_deterministic(payloads,coverage,monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('parser attempted filesystem I/O')
    monkeypatch.setattr(builtins,'open',forbidden)
    monkeypatch.setattr(io,'open',forbidden)
    monkeypatch.setattr(Path,'open',forbidden)
    c=parse(dict(reversed(tuple(payloads.items()))))
    assert c.fingerprint==coverage.fingerprint
    assert validate_project_coverage_source(c,payloads,manifest(payloads))==c


@pytest.mark.parametrize('path',('../bad.kicad_pro','C:/private.kicad_pro','dir\\bad.kicad_pro'))
def test_input_paths_rejected(payloads,path):
    with pytest.raises(ValueError,match='COVERAGE_PATH_INVALID'):
        parse_kicad_project_coverage(native_entry_relative_path=path,
            source_set_fingerprint=SOURCE_FP,payloads=payloads)


def test_supplied_file_handles_rejected(payloads):
    changed=dict(payloads); changed['arbitrary.bin']=io.BytesIO(b'bytes')
    with pytest.raises(ValueError,match='COVERAGE_PAYLOADS_INVALID'):
        parse_kicad_project_coverage(native_entry_relative_path=ENTRY, source_set_fingerprint=SOURCE_FP, payloads=changed)
@pytest.mark.parametrize('mode',('wrong_definition','unknown_member'))
def test_local_member_identity_not_satisfied_by_file_or_directory(payloads,mode):
    changed=dict(payloads)
    if mode=='wrong_definition':
        path='libs/pic_programmer_fp.pretty/RV2X4.kicad_mod'
        changed[path]=payloads[path].replace(b'(footprint "RV2X4"',b'(footprint "WrongMember"',1)
    else:
        changed[PCB]=payloads[PCB].replace(b'footprints:RV2X4',b'footprints:UnknownMember',1)
    c=parse(changed)
    assert c.dependency_state=='MISSING'
    assert any(d.kind=='FOOTPRINT_LIBRARY' and d.state=='MISSING' and d.source_file==PCB for d in c.dependencies)


def test_library_directory_identity_preserved(payloads,coverage):
    d=next(d for d in coverage.dependencies if d.source_file=='fp-lib-table' and d.reference.startswith('${KIPRJMOD}/'))
    assert d.state=='RESOLVED_IN_SOURCE'
    assert d.resolved_relative_path=='libs/pic_programmer_fp.pretty'
    r=next(r for r in coverage.relations if r.source_file==d.source_file and r.source_locator==d.source_locator and r.relation_type=='USES_LIBRARY')
    assert r.target_file is None and r.target_locator is None
    assert r.target_reference==d.reference and r.state=='RESOLVED_IN_SOURCE'
    assert d.resolved_relative_path not in {f.relative_path for f in coverage.files}


def test_quoted_delimiter_ambiguity_is_explicit(payloads):
    changed=dict(payloads)
    changed[PCB]=payloads[PCB].replace(b'(gr_text "',b'(gr_text "',1)
    changed[PCB]=changed[PCB].rstrip()[:-1]+b' (gr_text "(" (at 0 0) ")"))'
    c=parse(changed)
    assert c.coverage_state=='UNKNOWN'
    assert next(f for f in c.files if f.relative_path==PCB).parse_status=='FAILED'
    assert [u.reason for u in c.unknowns if u.source_file==PCB]==['PARSE_FAILURE']

def test_known_keyword_under_unrecognized_parent_is_unknown(payloads):
    changed=dict(payloads)
    changed[PCB]=payloads[PCB].rstrip()[:-1]+b' (future_parent (at 1 2)))'
    c=parse(changed)
    unknown=[u for u in c.unknowns if u.source_file==PCB]
    assert {u.native_keyword for u in unknown}=={'future_parent','at'}
    assert len(unknown)==2


def test_revalidation_detects_source_drift_without_optional_manifest(payloads,coverage):
    changed=dict(payloads); changed[PCB]+=b' '
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        validate_project_coverage_source(coverage,changed)


@pytest.mark.parametrize('mode',('model_header','table_version','extra_project_meta'))
def test_unsupported_source_grammar_never_complete(payloads,mode):
    changed=dict(payloads)
    if mode=='model_header':
        path='libs/3d_shapes/adjustable_rx2v4.wrl'
        changed[path]=payloads[path].replace(b'#VRML V2.0 utf8',b'#VRML V2.0 utf8INVALID',1)
    elif mode=='table_version':
        path='fp-lib-table'
        changed[path]=payloads[path].rstrip()[:-1]+b' (version))'
    else:
        path='extra.kicad_pro'; changed[path]=b'{"meta": []}'
    c=parse(changed)
    assert c.coverage_state=='UNKNOWN' and c.dependency_state=='UNKNOWN'
    assert next(f for f in c.files if f.relative_path==path).parse_status=='UNSUPPORTED'
    assert [u.reason for u in c.unknowns if u.source_file==path]==['UNKNOWN_FORMAT']


def test_missing_symbol_definition_relation_explicit(payloads):
    changed=dict(payloads)
    path='libs/pic_programmer_schlib.kicad_sym'
    changed[path]=payloads[path].replace(b'(extends "24C16")',b'(extends "MissingBase")',1)
    c=parse(changed)
    assert c.coverage_state=='PARTIAL'
    assert any(r.relation_type=='INSTANCE_OF' and r.target_reference=='MissingBase' and r.state=='MISSING' for r in c.relations)
    assert any(i.code=='RELATION_UNRESOLVED' for i in c.issues)

@pytest.mark.parametrize('table,family',(('fp-lib-table','footprints'),('sym-lib-table','pic_programmer_schlib')))
@pytest.mark.parametrize('entry_type',('Legacy','FutureProvider',''))
def test_unsupported_library_type_never_uri_resolves(payloads,table,family,entry_type):
    changed=dict(payloads)
    changed[table]=payloads[table].replace(b'(type "KiCad")',('(type "'+entry_type+'")').encode(),1)
    c=parse(changed)
    assert c.dependency_state=='UNKNOWN' and c.coverage_state=='PARTIAL'
    table_dependency=next(d for d in c.dependencies if d.source_file==table and d.reference.startswith('${KIPRJMOD}/'))
    assert table_dependency.state=='UNSUPPORTED' and table_dependency.resolved_relative_path is None
    uses=[d for d in c.dependencies if d.reference.startswith(family+':')]
    assert uses and all(d.state=='UNSUPPORTED' and d.resolved_relative_path is None for d in uses)
    relation=next(r for r in c.relations if r.source_file==table and r.source_locator==table_dependency.source_locator and r.relation_type=='USES_LIBRARY')
    assert relation.state=='UNSUPPORTED' and relation.target_file is None and relation.target_locator is None
    assert any(i.code=='RELATION_UNRESOLVED' and i.source_file==table and i.blocks_complete for i in c.issues)


def test_future_dependency_container_does_not_prove_dependency_set(payloads):
    changed=dict(payloads)
    changed[PCB]=payloads[PCB].rstrip()[:-1]+b' (future_dependencies (model "${KIPRJMOD}/unseen.wrl")))'
    c=parse(changed)
    assert c.coverage_state=='PARTIAL' and c.dependency_state=='UNKNOWN'
    unknown=[u for u in c.unknowns if u.source_file==PCB]
    assert {u.native_keyword for u in unknown}=={'future_dependencies','model'}
    assert all(u.reason=='UNKNOWN_KEYWORD' for u in unknown)


def test_exact_wrl_header_rechecked_against_supplied_source(payloads,coverage):
    path='libs/3d_shapes/adjustable_rx2v4.wrl'
    changed=dict(payloads)
    changed[path]=payloads[path].replace(b'#VRML V2.0 utf8',b'#VRML V2.0 utf8INVALID',1)
    value=mutable_envelope(coverage)
    digest=sha256(changed[path]).hexdigest()
    for file in value['files']:
        if file['relative_path']==path:
            file['sha256']=digest; file['byte_size']=len(changed[path])
    for unknown in value['unknowns']:
        if unknown['source_file']==path:
            unknown['source_file_sha256']=digest
    forged=ProjectCoverageV1.model_validate(value)
    with pytest.raises(ValueError,match='COVERAGE_SOURCE_BINDING_INVALID'):
        validate_project_coverage_source(forged,changed,manifest(changed))
