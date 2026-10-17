import { beforeEach, describe, expect, it } from "vitest";
import { nativeProjectRootErrorMessage, selectProjectRoot } from "../api/nativeProjectRoot";

describe("native project root capability", () => {
  beforeEach(() => {
    delete window.pywebview;
  });

  it("is unavailable in browser mode without a native bridge", async () => {
    await expect(selectProjectRoot()).resolves.toEqual({
      status: "UNAVAILABLE",
      path: null,
      reason_code: "NATIVE_FOLDER_PICKER_UNAVAILABLE",
    });
  });

  it("returns only the typed picker result", async () => {
    window.pywebview = { api: { select_project_root: async () => ({ status: "SELECTED", path: "D:\\projects", reason_code: null }) } };
    await expect(selectProjectRoot()).resolves.toEqual({ status: "SELECTED", path: "D:\\projects", reason_code: null });
  });

  it("maps bridge failures to a fixed capability error", async () => {
    window.pywebview = { api: { select_project_root: async () => { throw new Error("provider details"); } } };
    await expect(selectProjectRoot()).resolves.toEqual({ status: "FAILED", path: null, reason_code: "NATIVE_FOLDER_PICKER_FAILED" });
  });

  it("keeps internal picker reason codes out of the user-facing error", () => {
    expect(nativeProjectRootErrorMessage({ status: "FAILED", reason_code: "NATIVE_FOLDER_PICKER_FAILED" })).toBe("无法打开系统文件夹选择器，请重试。");
    expect(nativeProjectRootErrorMessage({ status: "UNAVAILABLE", reason_code: "PICKER_OWNER_UNAVAILABLE" })).toBe("当前系统无法打开文件夹选择器。");
  });
});
