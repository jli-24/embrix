from __future__ import annotations


COMPLETE_KICAD_BOARD = """(kicad_pcb
  (version 20240108)
  (generator pcbnew)
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "b.silkscreen")
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "+3V3")
  (net 3 "CLK_OUT")
  (footprint "Package_DFN_QFN:QFN-56"
    (layer "F.Cu")
    (at 10 20 90)
    (property "Reference" "U1")
    (property "Value" "ESP32-S3")
    (pad "1" smd rect (at -1 -1) (size 0.2 0.8) (layers "F.Cu") (net 1 "GND"))
    (pad "2" smd rect (at 0 -1) (size 0.2 0.8) (layers "F.Cu") (net 2 "+3V3"))
    (pad "3" smd rect (at 1 -1) (size 0.2 0.8) (layers "F.Cu") (net 3 "CLK_OUT"))
  )
  (segment (start 10 20) (end 11 20) (width 0.25) (layer "F.Cu") (net 1))
  (via (at 11 20) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
  (zone (net 1) (net_name "GND") (layer "F.Cu") (name "Ground fill"))
)"""


LEGACY_MODULE_BOARD = """(kicad_pcb
  (version 20211014)
  (generator pcbnew)
  (layers (0 F.Cu signal) (31 B.Cu signal))
  (net 1 GND)
  (module Package_QFP:LQFP-48
    (layer F.Cu)
    (at 5 6)
    (fp_text reference U2 (at 0 0))
    (fp_text value STM32F103 (at 0 1))
    (pad 1 thru_hole circle (at 0 0) (size 1 1) (drill 0.5) (layers *.Cu *.Mask) (net 1 GND))
  )
)"""


EMPTY_KICAD_BOARD = """(kicad_pcb
  (version 20240108)
  (generator pcbnew)
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
)"""


STACKED_PAD_BOARD = """(kicad_pcb
  (version 20240108)
  (generator pcbnew)
  (layers (0 "F.Cu" signal))
  (net 1 "BUTTON")
  (footprint "Switch:Stacked"
    (layer "F.Cu")
    (at 1 2)
    (property "Reference" "SW1")
    (property "Value" "BUTTON")
    (pad "1" smd rect (net 1 "BUTTON"))
    (pad "1" smd rect (net 1 "BUTTON"))
  )
)"""
