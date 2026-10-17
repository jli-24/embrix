export type KnowledgeEntityType = "HARDWARE" | "FIRMWARE" | "BUILD" | "VALIDATION" | "OPTIMIZATION" | "DECISION" | "CONSTRAINT";
export type KnowledgeConfidence = "VERIFIED" | "PROJECTED" | "UNVERIFIED";
export type KnowledgeRelationType = "USED_WITH" | "VALIDATED_BY" | "OPTIMIZED_BY" | "FAILED_BY" | "REPLACED_BY";
export type EngineeringKnowledgeNode = { node_id: string; project_id: string; entity_type: KnowledgeEntityType; reference: string; attributes: string[]; confidence: KnowledgeConfidence; fingerprint: string };
export type EngineeringKnowledgeRelation = { relation_id: string; source_id: string; target_id: string; relation_type: KnowledgeRelationType; evidence_reference: string; confidence: KnowledgeConfidence; fingerprint: string };
export type EngineeringKnowledgeSnapshot = { project_id: string; nodes: EngineeringKnowledgeNode[]; relations: EngineeringKnowledgeRelation[]; fingerprint: string };
export type KnowledgeSuggestion = { recommendation_id: string; project_id: string; matched_reference: string; reason: string; evidence_reference: string; confidence: KnowledgeConfidence; risk: string; fingerprint: string };
