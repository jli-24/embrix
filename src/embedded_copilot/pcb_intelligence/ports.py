from typing import Protocol, runtime_checkable

from embedded_copilot.pcb.parser.base import PCBParser


@runtime_checkable
class PCBParserPort(PCBParser, Protocol): ...


__all__ = ("PCBParserPort",)
