export type MultimodalInputType = "PCB" | "SCHEMATIC" | "DATASHEET" | "DOCUMENT" | "IMAGE";
export type VisionObservation = { observation_id: string; project_id: string; source_reference: string; observation_type: MultimodalInputType; content: string[]; confidence: number; fingerprint: string };
export type EngineeringInterpretation = { interpretation_id: string; observation_reference: string; summary: string; risk: string; confidence: number; fingerprint: string };
export type MultimodalAnalysisProjection = { observation: VisionObservation; interpretation: EngineeringInterpretation | null; fingerprint: string };
