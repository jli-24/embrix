import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ContextualApprovalCard, type ContextualApprovalOperation } from "./ContextualApprovalCard";

const operation: ContextualApprovalOperation = {
  operation_id: "operation-1",
  action: "FLASH",
  target: "device-1",
  risk_level: "HIGH",
  reason: "需要用户确认受控设备操作",
  summary: "写入固件并启动受控实验",
  safety_limits: ["电流 ≤ 3A"],
};

describe("ContextualApprovalCard", () => {
  it("projects a pending operation above the composer boundary", () => {
    const markup = renderToStaticMarkup(<ContextualApprovalCard operation={operation} onApprove={() => undefined} onCancel={() => undefined} />);
    expect(markup).toContain('data-operation-id="operation-1"');
    expect(markup).toContain("允许本次");
    expect(markup).toContain("取消");
    expect(markup).toContain("安全限制");
    expect(markup).toContain("我已经准备好执行烧录，请确认。");
    expect(markup).not.toContain("TASK_PENDING");
  });
});
