export type EngineeringConfidence = "VERIFIED" | "PROJECTED" | "UNVERIFIED";
export type EngineeringReviewCategory = "HARDWARE" | "FIRMWARE" | "INTERFACE" | "VALIDATION";
export type EngineeringReviewStatus = "PASS" | "WARNING" | "UNVERIFIED";
export type ValidationStatus = "VALID" | "REJECTED" | "UNAVAILABLE";

export type EngineeringRequirementProjection = {
  project_id: string;
  title: string;
  description: string;
  functional_requirements: string[];
  non_functional_requirements: string[];
  constraints: string[];
  interfaces: string[];
  confidence: EngineeringConfidence;
  fingerprint: string;
};

export type EngineeringArchitectureSnapshot = {
  project_id: string;
  components: string[];
  interfaces: string[];
  constraints: string[];
  decision_references: string[];
  confidence: EngineeringConfidence;
  fingerprint: string;
};

export type EngineeringInterfaceContract = {
  interface_id: string;
  project_id: string;
  producer: string;
  consumer: string;
  protocol: string;
  data_reference: string;
  constraints: string[];
  fingerprint: string;
};

export type EngineeringReviewSnapshot = {
  review_id: string;
  project_id: string;
  category: EngineeringReviewCategory;
  status: EngineeringReviewStatus;
  finding_reference: string;
  confidence: EngineeringConfidence;
  fingerprint: string;
};

export type EngineeringCompletionSnapshot = {
  project_id: string;
  requirement: EngineeringRequirementProjection;
  architecture: EngineeringArchitectureSnapshot;
  interfaces: EngineeringInterfaceContract[];
  reviews: EngineeringReviewSnapshot[];
  fingerprint: string;
};

export type EngineeringValidationResult = {
  project_id: string;
  snapshot_fingerprint: string;
  context_fingerprint: string;
  status: ValidationStatus;
  summary: string;
  fingerprint: string;
};
