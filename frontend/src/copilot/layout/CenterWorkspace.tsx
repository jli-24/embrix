import type { ComponentProps } from "react";

import { ChatPanel } from "../chat/ChatPanel";

export function CenterWorkspace(props: ComponentProps<typeof ChatPanel>) {
  return <ChatPanel {...props} />;
}
