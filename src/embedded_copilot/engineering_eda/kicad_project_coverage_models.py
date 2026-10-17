"""Exact, immutable source coverage contracts; no execution or design approval."""
from __future__ import annotations

from collections import Counter
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Literal
from pydantic import Field, field_validator, model_validator
from .contracts import EDAContract, FingerprintedEDAContract, fingerprint

CoverageNativeKind = Literal['PROJECT_SETTINGS', 'PCB_NET', 'FOOTPRINT', 'PAD', 'TRACK_SEGMENT', 'VIA', 'ZONE', 'BOARD_GRAPHIC', 'BOARD_TEXT', 'SCHEMATIC_SYMBOL', 'SHEET', 'WIRE', 'JUNCTION', 'NO_CONNECT', 'LABEL', 'POLYLINE', 'TEXT', 'SYMBOL_DEFINITION', 'FOOTPRINT_DEFINITION', 'MODEL_3D_REF', 'LIBRARY_ENTRY', 'PROPERTY', 'UNKNOWN']
DependencyState = Literal['RESOLVED_IN_SOURCE', 'EXTERNAL_UNVERIFIED', 'MISSING', 'UNSUPPORTED']
CoverageState = Literal['COMPLETE_FOR_DECLARED_SOURCE_OBJECTS', 'PARTIAL', 'UNKNOWN']
IssueCode = Literal['ENTRY_MISSING', 'FORMAT_UNSUPPORTED', 'PARSE_FAILURE', 'OBJECT_UNKNOWN', 'MODEL_GEOMETRY_OPAQUE', 'DEPENDENCY_EXTERNAL', 'DEPENDENCY_MISSING', 'RELATION_UNRESOLVED', 'SOURCE_CHANGED']

def relative_path(value: str) -> str:
    if type(value) is not str or not value or '\\' in value or value.startswith('/') or ':' in value:
        raise ValueError('COVERAGE_PATH_INVALID')
    if any(p in {'', '.', '..'} or unicodedata.normalize('NFC', p) != p or any(unicodedata.category(c).startswith('C') for c in p) for p in value.split('/')):
        raise ValueError('COVERAGE_PATH_INVALID')
    return value

class CoverageContract(EDAContract):
    @field_validator('source_file', 'relative_path', 'resolved_relative_path', 'target_file', 'project', 'schematic_root', 'board', check_fields=False)
    @classmethod
    def _path(cls, value):
        return None if value is None else relative_path(value)

    @field_validator('sha256', 'source_file_sha256', check_fields=False)
    @classmethod
    def _sha(cls, value):
        if type(value) is not str or re.fullmatch(r'[a-f0-9]{64}', value) is None:
            raise ValueError('COVERAGE_HASH_INVALID')
        return value

class CoverageSourceFile(CoverageContract):
    relative_path: str
    byte_size: int = Field(ge=0)
    sha256: str

class CoverageEntryFilesV1(CoverageContract):
    project: str
    schematic_root: str
    board: str

class CoverageFileV1(CoverageSourceFile):
    role: Literal['PROJECT_SETTINGS', 'SCHEMATIC', 'PCB', 'SYMBOL_LIBRARY', 'FOOTPRINT_LIBRARY_MEMBER', 'MODEL_3D', 'LIBRARY_TABLE', 'OTHER_SOURCE']
    format_marker: str | None
    parse_status: Literal['PARSED', 'OPAQUE', 'UNSUPPORTED', 'FAILED']

class CoverageSourceSpanV1(CoverageContract):
    byte_start: int = Field(ge=0)
    byte_length: int = Field(gt=0)
    sha256: str

class CoverageObjectV1(CoverageContract):
    source_file: str
    locator: str
    parent_locator: str | None
    native_kind: CoverageNativeKind
    native_id: str | None
    keyword: str
    values: tuple[str, ...]
    child_locators: tuple[str, ...]
    source_span: CoverageSourceSpanV1 | None

class CoverageRelationV1(CoverageContract):
    relation_type: Literal['CONTAINS', 'INSTANCE_OF', 'USES_LIBRARY', 'USES_MODEL', 'SHEET_PATH', 'CONNECTS_NET']
    source_file: str
    source_locator: str
    target_file: str | None
    target_locator: str | None
    target_reference: str
    state: DependencyState

class CoverageDependencyV1(CoverageContract):
    source_file: str
    source_locator: str
    reference: str
    kind: Literal['SCHEMATIC_SHEET', 'SYMBOL_LIBRARY', 'FOOTPRINT_LIBRARY', 'MODEL_3D']
    resolved_relative_path: str | None
    state: DependencyState

class CoverageUnknownV1(CoverageContract):
    source_file: str
    locator: str
    native_keyword: str
    source_file_sha256: str
    source_span: CoverageSourceSpanV1 | None
    reason: Literal['UNKNOWN_KEYWORD', 'UNKNOWN_FORMAT', 'OPAQUE_GEOMETRY', 'PARSE_FAILURE', 'UNSUPPORTED_SEMANTICS']

class CoverageTypeCountV1(CoverageContract):
    native_kind: CoverageNativeKind
    count: int = Field(ge=0)

class CoverageCountsV1(CoverageContract):
    objects_total: int = Field(ge=0)
    recognized_total: int = Field(ge=0)
    unknown_total: int = Field(ge=0)
    by_kind: tuple[CoverageTypeCountV1, ...]

class CoverageIssueV1(CoverageContract):
    code: IssueCode
    source_file: str
    locator: str | None
    blocks_complete: bool

    @model_validator(mode='after')
    def _blocking(self):
        if self.blocks_complete != (self.code not in {'DEPENDENCY_EXTERNAL', 'DEPENDENCY_MISSING'}):
            raise ValueError('COVERAGE_ISSUE_BLOCK_INVALID')
        return self

def aggregate_dependency_state(dependencies, files, unknowns=()) -> str:
    if any(u.reason != 'OPAQUE_GEOMETRY' for u in unknowns):
        return 'UNKNOWN'
    if any(f.parse_status in {'FAILED', 'UNSUPPORTED'} for f in files) or any(d.state == 'UNSUPPORTED' for d in dependencies):
        return 'UNKNOWN'
    states = {d.state for d in dependencies}
    return next((s for s in ('MISSING', 'EXTERNAL_UNVERIFIED') if s in states), 'RESOLVED_IN_SOURCE')

def aggregate_coverage_state(files, unknowns, issues) -> str:
    if any(i.code in {'ENTRY_MISSING', 'FORMAT_UNSUPPORTED', 'PARSE_FAILURE', 'SOURCE_CHANGED'} for i in issues):
        return 'UNKNOWN'
    if unknowns or any(f.parse_status != 'PARSED' for f in files) or any(i.blocks_complete for i in issues):
        return 'PARTIAL'
    return 'COMPLETE_FOR_DECLARED_SOURCE_OBJECTS'

class ProjectCoverageV1(FingerprintedEDAContract):
    schema: Literal['kicad-project-source-coverage-v1']
    profile_id: Literal['KICAD_PROJECT_SOURCE_20240108_V1']
    source_set_fingerprint: str
    entry_files: CoverageEntryFilesV1
    files: tuple[CoverageFileV1, ...]
    objects: tuple[CoverageObjectV1, ...]
    relations: tuple[CoverageRelationV1, ...]
    dependencies: tuple[CoverageDependencyV1, ...]
    unknowns: tuple[CoverageUnknownV1, ...]
    counts: CoverageCountsV1
    coverage_state: CoverageState
    dependency_state: Literal['RESOLVED_IN_SOURCE', 'EXTERNAL_UNVERIFIED', 'MISSING', 'UNKNOWN']
    issues: tuple[CoverageIssueV1, ...]

    @field_validator('source_set_fingerprint')
    @classmethod
    def _source_fp(cls, value):
        return fingerprint(value)

    @model_validator(mode='after')
    def _integrity(self):
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len({p.casefold() for p in paths}) != len(paths):
            raise ValueError('COVERAGE_FILES_INVALID')
        files = {f.relative_path: f for f in self.files}
        if not {self.entry_files.project, self.entry_files.schematic_root, self.entry_files.board} <= files.keys():
            raise ValueError('COVERAGE_ENTRIES_INVALID')
        keys = [(o.source_file, o.locator) for o in self.objects]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError('COVERAGE_OBJECTS_INVALID')
        objects = dict(zip(keys, self.objects))
        native_ids = [(o.source_file, o.native_id) for o in self.objects if o.native_id is not None]
        if len(set(native_ids)) != len(native_ids):
            raise ValueError('COVERAGE_DUPLICATE_NATIVE_ID')
        children = {}
        for item in self.objects:
            children.setdefault((item.source_file, item.parent_locator), set()).add(item.locator)
        for o in self.objects:
            if o.source_file not in files or not o.locator.startswith('/'):
                raise ValueError('COVERAGE_SOURCE_INVALID')
            if o.parent_locator is not None and (o.source_file, o.parent_locator) not in objects:
                raise ValueError('COVERAGE_PARENT_INVALID')
            expected = children.get((o.source_file, o.locator), set())
            if len(set(o.child_locators)) != len(o.child_locators) or set(o.child_locators) != expected:
                raise ValueError('COVERAGE_CHILDREN_INVALID')
            if o.source_span is not None and o.source_span.byte_start + o.source_span.byte_length > files[o.source_file].byte_size:
                raise ValueError('COVERAGE_SPAN_INVALID')
        unknown_keys = [(u.source_file, u.locator) for u in self.unknowns]
        expected_unknown = {k for k, o in objects.items() if o.native_kind == 'UNKNOWN'}
        if unknown_keys != sorted(unknown_keys) or len(set(unknown_keys)) != len(unknown_keys) or set(unknown_keys) != expected_unknown:
            raise ValueError('COVERAGE_UNKNOWN_INVALID')
        for u in self.unknowns:
            o = objects[(u.source_file, u.locator)]
            if u.native_keyword != o.keyword or u.source_span != o.source_span or u.source_file_sha256 != files[u.source_file].sha256:
                raise ValueError('COVERAGE_UNKNOWN_BINDING_INVALID')
        counts = Counter(o.native_kind for o in self.objects)
        expected_counts = tuple((k, counts[k]) for k in sorted(counts))
        if tuple((c.native_kind, c.count) for c in self.counts.by_kind) != expected_counts or self.counts.objects_total != len(objects) or self.counts.unknown_total != len(self.unknowns) or self.counts.recognized_total != len(objects) - len(self.unknowns):
            raise ValueError('COVERAGE_COUNTS_INVALID')
        directory_dependencies = set()
        for d in self.dependencies:
            source = objects.get((d.source_file, d.source_locator))
            declared_directory = False
            if (source is not None and source.native_kind == 'LIBRARY_ENTRY'
                    and PurePosixPath(d.source_file).name == 'fp-lib-table'
                    and d.kind == 'FOOTPRINT_LIBRARY'
                    and d.reference.startswith('${KIPRJMOD}/')
                    and d.resolved_relative_path is not None
                    and d.resolved_relative_path.endswith('.pretty')):
                tail = relative_path(d.reference[len('${KIPRJMOD}/'):])
                expected_path = str(PurePosixPath(self.entry_files.project).parent / tail)
                declared_directory = (d.resolved_relative_path == expected_path
                    and any(f.role == 'FOOTPRINT_LIBRARY_MEMBER'
                        and f.relative_path.startswith(expected_path + '/') for f in self.files))
            if source is None or (d.state == 'RESOLVED_IN_SOURCE'
                    and d.resolved_relative_path not in files and not declared_directory):
                raise ValueError('COVERAGE_DEPENDENCY_INVALID')
            if declared_directory and d.state == 'RESOLVED_IN_SOURCE':
                directory_dependencies.add((d.source_file, d.source_locator, d.reference))
        dep_keys = [(d.source_file, d.source_locator, d.kind, d.reference) for d in self.dependencies]
        if dep_keys != sorted(dep_keys) or len(set(dep_keys)) != len(dep_keys):
            raise ValueError('COVERAGE_DEPENDENCY_ORDER_INVALID')
        relation_keys = [(r.source_file, r.source_locator, r.relation_type, r.target_file or '', r.target_locator or '', r.target_reference) for r in self.relations]
        if relation_keys != sorted(relation_keys) or len(set(relation_keys)) != len(relation_keys):
            raise ValueError('COVERAGE_RELATIONS_INVALID')
        for r in self.relations:
            if (r.source_file, r.source_locator) not in objects or (r.target_file is not None and r.target_locator is not None and (r.target_file, r.target_locator) not in objects):
                raise ValueError('COVERAGE_RELATION_TARGET_INVALID')
            directory_relation = (r.relation_type == 'USES_LIBRARY'
                and r.target_file is None and r.target_locator is None
                and (r.source_file, r.source_locator, r.target_reference) in directory_dependencies)
            if r.state == 'RESOLVED_IN_SOURCE' and (r.target_file is None or r.target_file not in files) and not directory_relation:
                raise ValueError('COVERAGE_RELATION_STATE_INVALID')
        for i in self.issues:
            if i.source_file not in files or (i.locator is not None and (i.source_file, i.locator) not in objects):
                raise ValueError('COVERAGE_ISSUE_SOURCE_INVALID')
        issue_keys = [(i.source_file, i.locator or '', i.code) for i in self.issues]
        if issue_keys != sorted(issue_keys) or len(set(issue_keys)) != len(issue_keys):
            raise ValueError('COVERAGE_ISSUES_INVALID')
        if self.coverage_state != aggregate_coverage_state(self.files, self.unknowns, self.issues) or self.dependency_state != aggregate_dependency_state(self.dependencies, self.files, self.unknowns):
            raise ValueError('COVERAGE_AGGREGATION_INVALID')
        return self