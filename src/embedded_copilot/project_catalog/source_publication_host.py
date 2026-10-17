"""Trusted composition capability for explicit project-local Source publication."""

from __future__ import annotations

from .source_publication import (
    ComponentSourcePublishCommandV1,
    PublishedComponentSourcePort,
    TrustedComponentSourcePublisher,
)


class CatalogSourcePublicationHost:
    """Caller supplies already qualified, permitted local material; no HTTP importer.

    Local-use qualification and permission remain the trusted host's prerequisite.
    This capability does not grant redistribution, discover libraries, or publish
    the package resource at startup. Fixture publication is only mechanism evidence.
    """

    publisher_id = "catalog-local-reviewed-source-v1"

    def __init__(self, catalog):
        self._catalog = catalog
        self._repository = catalog._repository

    def publish(
        self,
        *,
        tenant_id: str,
        project_id: str,
        requester: str,
        command: ComponentSourcePublishCommandV1,
    ):
        self._catalog.get_project_for_requester(
            project_id, tenant_id=tenant_id, requester=requester
        )
        return TrustedComponentSourcePublisher(
            self._repository,
            tenant_id=tenant_id,
            project_id=project_id,
            publisher_id=self.publisher_id,
            requester=requester,
        ).publish(command)

    def source_for(self, *, tenant_id: str, project_id: str, requester: str):
        self._catalog.get_project_for_requester(
            project_id, tenant_id=tenant_id, requester=requester
        )
        publications = self._repository.published_component_source_heads_for_host(
            tenant_id=tenant_id, project_id=project_id, publisher_id=self.publisher_id
        )
        if not publications:
            return None
        return PublishedComponentSourcePort(
            self._repository, tenant_id=tenant_id, publisher_id=self.publisher_id
        )
