from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator

from embedded_copilot.component_recommendation.catalog import StaticComponentSource
from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.engineering_runtime.common import (
    RuntimeContract, canonical_fingerprint, validate_fingerprint, validate_identifier,
)


class ComponentSourcePublicationV1(RuntimeContract):
    schema_version: Literal[1]
    tenant_id: str
    project_id: str
    publisher_id: str
    source_id: str
    publication_id: str
    generation: int
    predecessor_publication_fingerprint: str | None
    record: ComponentSourceRecord
    fingerprint: str

    @field_validator('tenant_id', 'project_id', 'publisher_id', 'source_id', 'publication_id', mode='before')
    @classmethod
    def _identity(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator('fingerprint', 'predecessor_publication_fingerprint', mode='before')
    @classmethod
    def _fingerprint(cls, value: object, info) -> str | None:
        return None if value is None and info.field_name == 'predecessor_publication_fingerprint' else validate_fingerprint(value, field=info.field_name)

    @field_validator('generation', mode='before')
    @classmethod
    def _generation(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError('source generation must be a positive integer')
        return value

    @model_validator(mode='after')
    def _binding(self):
        if self.source_id != self.record.source_id:
            raise ValueError('source publication record identity mismatch')
        if (self.generation == 1) != (self.predecessor_publication_fingerprint is None):
            raise ValueError('source publication predecessor is invalid')
        if self.fingerprint != canonical_fingerprint(self, exclude={'fingerprint'}):
            raise ValueError('source publication fingerprint mismatch')
        return self

    @classmethod
    def create(cls, **values: object) -> 'ComponentSourcePublicationV1':
        material = {**values, 'fingerprint': 'sha256:' + '0' * 64}
        material['fingerprint'] = canonical_fingerprint(cls.model_construct(**material), exclude={'fingerprint'})
        return cls.model_validate(material)


class ComponentSourcePublishCommandV1(RuntimeContract):
    schema_version: Literal[1]
    request_id: str
    source_id: str
    expected_head_publication_fingerprint: str | None
    record: ComponentSourceRecord

    @field_validator('request_id', 'source_id', mode='before')
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator('expected_head_publication_fingerprint', mode='before')
    @classmethod
    def _expected(cls, value: object) -> str | None:
        return None if value is None else validate_fingerprint(value, field='expected_head_publication_fingerprint')

    @model_validator(mode='after')
    def _record_identity(self):
        if self.source_id != self.record.source_id:
            raise ValueError('source publish record identity mismatch')
        return self


class PublishedComponentSourcePort:
    """Internal read-side projection of one Catalog source head per project."""

    __slots__ = ('_repository', '_tenant_id', '_publisher_id')

    def __init__(self, repository: object, *, tenant_id: str, publisher_id: str) -> None:
        self._repository = repository
        self._tenant_id = validate_identifier(tenant_id, field='tenant_id')
        self._publisher_id = validate_identifier(publisher_id, field='publisher_id')

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def publisher_id(self) -> str:
        return self._publisher_id

    @property
    def repository(self) -> object:
        return self._repository

    def current_publication(self, project_id: str, source_id: str) -> ComponentSourcePublicationV1 | None:
        publication = self._repository.current_component_source_publication(
            tenant_id=self.tenant_id, project_id=project_id, source_id=source_id,
        )
        if publication is not None and publication.publisher_id != self.publisher_id:
            raise ValueError('source publisher changed')
        return publication

    def search(self, project_id: str, architecture_id: str, requirement: object) -> tuple[ComponentSourceRecord, ...]:
        publications = self._repository.current_component_source_publications(
            tenant_id=self.tenant_id, project_id=project_id,
        )
        if any(item.publisher_id != self.publisher_id for item in publications):
            raise ValueError('source publisher changed')
        if not publications:
            return ()
        return StaticComponentSource(tuple(item.record for item in publications)).search(
            project_id, architecture_id, requirement,
        )


class TrustedComponentSourcePublisher:
    """Composition-held capability; request callers cannot choose scope or publisher."""

    __slots__ = ('_repository', '_tenant_id', '_project_id', '_publisher_id', '_requester')

    def __init__(self, repository: object, *, tenant_id: str, project_id: str,
                 publisher_id: str, requester: str | None = None) -> None:
        self._repository = repository
        self._tenant_id = validate_identifier(tenant_id, field='tenant_id')
        self._project_id = validate_identifier(project_id, field='project_id')
        self._publisher_id = validate_identifier(publisher_id, field='publisher_id')
        self._requester = None if requester is None else validate_identifier(requester, field='requester')

    def publish(self, command: ComponentSourcePublishCommandV1) -> tuple[ComponentSourcePublicationV1, bool]:
        checked = ComponentSourcePublishCommandV1.model_validate(command.model_dump(mode='python'))
        return self._repository.publish_component_source(
            tenant_id=self._tenant_id, project_id=self._project_id,
            publisher_id=self._publisher_id, request_id=checked.request_id,
            source_id=checked.source_id,
            expected_head_publication_fingerprint=checked.expected_head_publication_fingerprint,
            record=checked.record, requester=self._requester,
        )
