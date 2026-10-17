import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatInput } from "../chat/ChatInput";
import { ProjectExplorer } from "../project/ProjectExplorer";
import { SettingsPanel } from "../settings/SettingsPanel";

const fingerprint = `sha256:${"a".repeat(64)}`;
const project = { project_id: "project-1", tenant_id: "tenant", name: "Gateway", location_id: "location", directory_name: "gateway", creation_mode: "CREATE_NEW" as const, relative_path: "gateway", project_type: "ESP_IDF" as const, initialization_status: "READY" as const, platform: "ESP32-S3", version: null, branch: "main", failure_code: null, created_at: "2026-08-10T14:30:00Z", updated_at: "2026-08-10T14:30:00Z", last_activity_at: "2026-08-10T14:30:00Z", fingerprint };
const conversation = { conversation_id: "conversation-1", project_id: "project-1", title: "I2C 分析", turn_count: 1, created_at: "2026-08-10T14:30:00Z", updated_at: "2026-08-10T14:30:00Z", fingerprint };

describe("v5.2.3 visual contract", () => {
  it("keeps active project and active conversation navigation distinct", () => {
    const markup = renderToStaticMarkup(<ProjectExplorer projects={[project]} activeProjectId="project-1" activeConversationId="conversation-1" conversations={[conversation]} catalogError={null} settingsOpen={false} onProjectChange={() => undefined} onNewProject={() => undefined} onNewConversation={() => undefined} onConversationChange={() => undefined} onToggleSettings={() => undefined} />);
    expect(markup).toContain("ide-project-row is-active");
    expect(markup).toContain("ide-history-list");
    expect(markup).toContain("aria-label=\"新建项目\"");
    expect(markup).toContain("class=\"is-active\"");
    expect(markup).not.toContain("新建项目</button>");
  });

  it("keeps the minimal attach and arrow-only send actions in the composer", () => {
    const markup = renderToStaticMarkup(<ChatInput connected={false} pending={false} references={[]} selected={[]} onToggleReference={() => undefined} onAttach={() => undefined} onSubmit={() => undefined} />);
    expect(markup).toContain("ide-composer__editor");
    expect(markup).toContain("ide-composer__toolbar");
    expect(markup).toContain("ide-send-button");
    expect(markup).toContain("lucide-arrow-up");
    expect(markup).not.toContain("ide-context-menu");
    expect(markup).not.toContain("Research");
    expect(markup).not.toContain("添加工程上下文");
    expect(markup).not.toContain("Enter 发送");
    expect(markup).not.toContain(">Send<");
  });

  it("shows the production Provider Management list and optional Ollama status", () => {
    const markup = renderToStaticMarkup(<SettingsPanel preferences={{ theme: "light", density: "compact", answerDetail: "standard", evidenceDisplay: "expanded" }} onChange={() => undefined} onClose={() => undefined} capabilities={[
      { capability: "CHAT_ANALYSIS", status: "DEGRADED", reason_code: "MODEL_GATEWAY_UNAVAILABLE", detail: "runtime", runtime_status: "available", fingerprint },
      { capability: "PROVIDER_CONFIGURATION", status: "AVAILABLE", reason_code: "CONFIGURED", detail: "provider", fingerprint },
      { capability: "CHAT_MODEL", status: "UNAVAILABLE", reason_code: "PROVIDER_CONNECTION_UNVERIFIED", detail: "model", fingerprint },
    ]} />);
    expect(markup).toContain("ide-settings ide-settings--open");
    expect(markup).toContain("Ollama");
    expect(markup).toContain("提供商管理");
    expect(markup).toContain("添加模型提供商");
    expect(markup).toContain("尚未保存模型提供商连接");
    expect(markup).not.toContain("选择提供商模板");
    expect(markup).not.toContain("API Key");
    expect(markup).toContain("提供商");
    expect(markup).toContain("模型");
    expect(markup).toContain("LOADING");
    expect(markup).not.toContain("Credential</strong> 未配置");
    expect(markup).not.toContain("EMBRIX_SECRET_CANARY");
    expect(markup).not.toContain("添加位置");
    expect(markup).not.toContain("例如 D:\\STM32Projects");
  });
});
