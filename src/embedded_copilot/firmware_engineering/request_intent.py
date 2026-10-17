from __future__ import annotations

import re
import unicodedata


def is_firmware_build_request(value: object) -> bool:
    """Recognize the existing explicit command style without adding a UI mode."""

    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[\s，。！？、,.!?]+", "", unicodedata.normalize("NFKC", value)).casefold()
    return normalized in {
        "开始构建固件",
        "构建固件",
        "开始编译固件",
        "编译固件",
        "构建项目",
        "开始构建项目",
        "编译项目",
        "开始编译项目",
        "build一下",
        "build项目",
        "buildfirmware",
        "checkfirmwarebuild",
        "检查固件能不能编译",
        "检查固件是否能编译",
        "startfirmwarebuild",
    }
