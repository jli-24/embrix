import type { AuditView } from "../api/environmentClient";
import type { ContextReference } from "../api/chatClient";

export type DemoEvidence = {
  referenceId: string;
  type: "HIL Observation" | "Power Analysis" | "Firmware Analysis";
  status: "OBSERVED" | "ANALYZED" | "COMPLETED";
  fingerprint: string;
  timestamp: string;
  metric: string;
};

export const demoEvidence: DemoEvidence[] = [
  { referenceId: "obs_20250810_1430", type: "HIL Observation", status: "OBSERVED", fingerprint: `sha256:${"a".repeat(64)}`, timestamp: "2026-08-10T14:30:25+08:00", metric: "Power 120mA" },
  { referenceId: "pwr_20250810_1420", type: "Power Analysis", status: "ANALYZED", fingerprint: `sha256:${"b".repeat(64)}`, timestamp: "2026-08-10T14:20:10+08:00", metric: "Average power 120mA" },
  { referenceId: "fw_20250810_1305", type: "Firmware Analysis", status: "COMPLETED", fingerprint: `sha256:${"c".repeat(64)}`, timestamp: "2026-08-10T13:05:18+08:00", metric: "Wi-Fi power mode" },
];

export const demoProposal: ContextReference = {
  tenant_id: "desktop-tenant",
  project_id: "esp32-s3-sensor-gateway",
  reference_id: "proposal_20250810_1440",
  reference_kind: "PROPOSAL",
  source_fingerprint: `sha256:${"e".repeat(64)}`,
  display_label: "Reduce I2C polling frequency",
  fingerprint: `sha256:${"e".repeat(64)}`,
};

export const demoContextReferences: ContextReference[] = [
  ...demoEvidence.map((evidence): ContextReference => ({
    tenant_id: "desktop-tenant",
    project_id: "esp32-s3-sensor-gateway",
    reference_id: evidence.referenceId,
    reference_kind: "EVIDENCE",
    source_fingerprint: evidence.fingerprint,
    display_label: evidence.type,
    fingerprint: evidence.fingerprint,
  })),
  demoProposal,
];

export const demoAuditProjection: AuditView[] = [
  { event_type: "HIL_UART_OBSERVED", object_reference: "obs_20250810_1430", status: "NORMAL", timestamp: "2026-08-10T14:30:25+08:00" },
  { event_type: "HIL_GPIO_OBSERVED", object_reference: "gpio_20250810_1430", status: "NORMAL", timestamp: "2026-08-10T14:30:25+08:00" },
  { event_type: "HIL_I2C_OBSERVED", object_reference: "i2c_20250810_1430", status: "WARNING", timestamp: "2026-08-10T14:30:25+08:00" },
  { event_type: "HIL_POWER_OBSERVED", object_reference: "power_20250810_1430", status: "120mA", timestamp: "2026-08-10T14:30:25+08:00" },
  { event_type: "EDA_ERC_CHECK", object_reference: "erc_20250810_1420", status: "PASS", timestamp: "2026-08-10T14:20:10+08:00" },
  { event_type: "EDA_DRC_CHECK", object_reference: "drc_20250810_1420", status: "PASS", timestamp: "2026-08-10T14:20:10+08:00" },
  { event_type: "EDA_BOM_CHECK", object_reference: "bom_20250810_1420", status: "PASS", timestamp: "2026-08-10T14:20:10+08:00" },
];
