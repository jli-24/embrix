export type NativeProjectRootSelection = {
  status: "SELECTED" | "CANCELLED" | "UNAVAILABLE" | "FAILED";
  path: string | null;
  reason_code: string | null;
};

/** Keep presentation-safe capability failures separate from internal reason codes. */
export function nativeProjectRootErrorMessage(selection: Pick<NativeProjectRootSelection, "status" | "reason_code">): string {
  if (selection.status === "UNAVAILABLE") {
    return "当前系统无法打开文件夹选择器。";
  }
  return "无法打开系统文件夹选择器，请重试。";
}

declare global {
  interface Window {
    pywebview?: {
      api?: {
        select_project_root?: () => Promise<NativeProjectRootSelection>;
      };
    };
  }
}

/** Presentation-only capability. Browser mode is deliberately unavailable. */
export async function selectProjectRoot(): Promise<NativeProjectRootSelection> {
  const method = window.pywebview?.api?.select_project_root;
  if (typeof method !== "function") {
    return { status: "UNAVAILABLE", path: null, reason_code: "NATIVE_FOLDER_PICKER_UNAVAILABLE" };
  }
  try {
    const result = await method();
    if (!result || !["SELECTED", "CANCELLED", "UNAVAILABLE", "FAILED"].includes(result.status)) {
      return { status: "FAILED", path: null, reason_code: "NATIVE_FOLDER_PICKER_FAILED" };
    }
    return result;
  } catch {
    return { status: "FAILED", path: null, reason_code: "NATIVE_FOLDER_PICKER_FAILED" };
  }
}
