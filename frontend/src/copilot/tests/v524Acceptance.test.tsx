import { describe, expect, it } from "vitest";
import { populatedWorkspaceFixture, unavailableWorkspaceFixture } from "../acceptanceFixtures/workspaceFixtures";

describe("v5.2.4 acceptance workspace fixtures", () => {
  it("keeps one active Project identity across the populated workspace", () => {
    const fixture = populatedWorkspaceFixture;
    expect(fixture.projects.map((item) => item.name)).toEqual(["ESP32-S3 Sensor Gateway", "Smart Robot Controller", "Power Monitor System"]);
    expect(fixture.project?.project_id).toBe(fixture.activeProjectId);
    expect(fixture.conversation?.project_id).toBe(fixture.activeProjectId);
    expect(fixture.chatProject?.project_id).toBe(fixture.activeProjectId);
    expect(fixture.context?.project_id).toBe(fixture.activeProjectId);
    expect(fixture.conversation?.turns[0]?.evidence.map((item) => item.label)).toEqual(["sensor_i2c.c", "I2C timeout observation", "Power integrity review"]);
  });

  it("uses engineering-reading evidence fields and read-only recommendations", () => {
    const turn = populatedWorkspaceFixture.conversation?.turns[0];
    expect(turn?.evidence.every((item) => item.reference_id && item.status && item.source_type && item.label)).toBe(true);
    expect(turn?.recommendations.every((item) => item.status === "Proposal")).toBe(true);
    expect(JSON.stringify(populatedWorkspaceFixture).toLowerCase()).not.toMatch(/raw log|prompt|token|secret|execute|build|flash/);
  });

  it("keeps unavailable fixture fail-closed and free of populated project data", () => {
    expect(unavailableWorkspaceFixture.projects).toHaveLength(0);
    expect(unavailableWorkspaceFixture.project).toBeNull();
    expect(unavailableWorkspaceFixture.conversation).toBeNull();
    expect(unavailableWorkspaceFixture.context).toBeNull();
    expect(unavailableWorkspaceFixture.chatAvailable).toBe(false);
    expect(unavailableWorkspaceFixture.catalogError).toBe("Project Catalog 不可用");
  });
});
