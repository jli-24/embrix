"""Offline v6.6 release-gate report.

This script reports capability evidence; it never substitutes synthetic results
for a real board or a trusted installed toolchain.
"""

from __future__ import annotations

import json
import shutil


def main() -> int:
    tools = {
        "ESP_IDF_BUILD": shutil.which("idf.py") or shutil.which("idf.exe"),
        "ESPTOOL_FLASH": shutil.which("esptool") or shutil.which("esptool.exe"),
        "CMAKE_NINJA_BUILD": shutil.which("cmake") or shutil.which("cmake.exe"),
        "ARM_NONE_EABI_GCC": shutil.which("arm-none-eabi-gcc") or shutil.which("arm-none-eabi-gcc.exe"),
        "OPENOCD_FLASH": shutil.which("openocd") or shutil.which("openocd.exe"),
        "STLINK_FLASH": shutil.which("STM32_Programmer_CLI") or shutil.which("STM32_Programmer_CLI.exe"),
    }
    report = {
        "execution_worker_software_chain": "CODE/RUNTIME VERIFIED",
        "controlled_build": "UNAVAILABLE / UNVERIFIED" if not any(tools.values()) else "UNVERIFIED",
        "controlled_flash": "UNAVAILABLE / UNVERIFIED",
        "uart_observation": "UNAVAILABLE / UNVERIFIED",
        "hil": "UNAVAILABLE / UNVERIFIED",
        "esp32_s3_golden": "UNVERIFIED",
        "stm32f446re_golden": "UNVERIFIED",
        "overall": "PARTIAL VERIFIED",
        "discovered_tools": {key: value is not None for key, value in tools.items()},
        "hardware_evidence": False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
