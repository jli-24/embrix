from __future__ import annotations

from .exceptions import PCBModelConflict
from .models import (
    EngineeringPCBGraph,
    PCBContext,
    PCBGraphEdge,
    PCBGraphEdgeType,
    PCBGraphNode,
    PCBGraphNodeType,
    deterministic_id,
)


class DeterministicPCBGraphBuilder:
    def build(self, context: PCBContext) -> EngineeringPCBGraph:
        checked = PCBContext.model_validate(context.model_dump(mode="python"))
        nodes: dict[str, PCBGraphNode] = {}
        edges: dict[str, PCBGraphEdge] = {}

        def add_node(
            node_type: PCBGraphNodeType,
            identity: str,
            source_fingerprint: str,
            evidence_refs,
        ):
            node_id = deterministic_id(
                "node", checked.fingerprint, node_type.value, identity
            )
            node = PCBGraphNode.create(
                node_id=node_id,
                node_type=node_type,
                identity=identity,
                source_fingerprint=source_fingerprint,
                evidence_refs=tuple(evidence_refs),
            )
            existing = nodes.get(node_id)
            if existing is not None and existing != node:
                raise PCBModelConflict("graph node identity conflict")
            nodes[node_id] = node
            return node

        def add_edge(
            edge_type: PCBGraphEdgeType,
            source: PCBGraphNode,
            target: PCBGraphNode,
            evidence_refs,
        ):
            edge_id = deterministic_id(
                "edge",
                checked.fingerprint,
                edge_type.value,
                source.node_id,
                target.node_id,
            )
            edge = PCBGraphEdge.create(
                edge_id=edge_id,
                edge_type=edge_type,
                source_node_id=source.node_id,
                target_node_id=target.node_id,
                evidence_refs=tuple(evidence_refs),
            )
            existing = edges.get(edge_id)
            if existing is not None and existing != edge:
                raise PCBModelConflict("graph edge identity conflict")
            edges[edge_id] = edge

        component_nodes = {}
        pin_nodes = {}
        net_nodes = {}
        for component in checked.components:
            component_node = add_node(
                PCBGraphNodeType.COMPONENT,
                component.reference,
                component.fingerprint,
                component.evidence_refs,
            )
            component_nodes[component.reference] = component_node
            for pin in component.pins:
                identity = f"{component.reference}:{pin.pin_number}"
                pin_node = add_node(
                    PCBGraphNodeType.PIN, identity, pin.fingerprint, pin.evidence_refs
                )
                pin_nodes[(component.reference, pin.pin_number)] = pin_node
                add_edge(
                    PCBGraphEdgeType.HAS_PIN,
                    component_node,
                    pin_node,
                    pin.evidence_refs,
                )
        for net in checked.nets:
            net_nodes[net.net_id] = add_node(
                PCBGraphNodeType.NET, net.net_id, net.fingerprint, net.evidence_refs
            )
        for connection in checked.connections:
            pin_node = pin_nodes.get(
                (connection.component_reference, connection.pin_number)
            )
            net_node = net_nodes.get(connection.net_id)
            if pin_node is None or net_node is None:
                raise PCBModelConflict(
                    "graph connection references an unknown pin or net"
                )
            add_edge(
                PCBGraphEdgeType.PART_OF_NET,
                pin_node,
                net_node,
                connection.evidence_refs,
            )
        for item in checked.bom_items:
            item_node = add_node(
                PCBGraphNodeType.BOM_ITEM,
                item.item_id,
                item.fingerprint,
                item.evidence_refs,
            )
            for reference in item.references:
                component_node = component_nodes.get(reference)
                if component_node is not None:
                    add_edge(
                        PCBGraphEdgeType.MATCHES_BOM,
                        component_node,
                        item_node,
                        item.evidence_refs,
                    )
        for datasheet in checked.datasheets:
            component_node = component_nodes.get(datasheet.component_reference)
            if component_node is None:
                raise PCBModelConflict("datasheet references an unknown component")
            datasheet_node = add_node(
                PCBGraphNodeType.DATASHEET,
                datasheet.component_reference,
                datasheet.fingerprint,
                datasheet.evidence_refs,
            )
            add_edge(
                PCBGraphEdgeType.DESCRIBED_BY,
                component_node,
                datasheet_node,
                datasheet.evidence_refs,
            )
        for interface in checked.interfaces:
            interface_node = add_node(
                PCBGraphNodeType.INTERFACE,
                interface.interface_id,
                interface.fingerprint,
                interface.evidence_refs,
            )
            for net_id in interface.signal_nets:
                net_node = net_nodes.get(net_id)
                if net_node is None:
                    raise PCBModelConflict("interface references an unknown net")
                add_edge(
                    PCBGraphEdgeType.IMPLEMENTS_INTERFACE,
                    net_node,
                    interface_node,
                    interface.evidence_refs,
                )
        return EngineeringPCBGraph.create(
            graph_id=deterministic_id(
                "pcb-graph", checked.project_id, checked.fingerprint
            ),
            project_id=checked.project_id,
            context_fingerprint=checked.fingerprint,
            nodes=tuple(sorted(nodes.values(), key=lambda item: item.node_id)),
            edges=tuple(sorted(edges.values(), key=lambda item: item.edge_id)),
            evidence_refs=checked.provenance,
        )
