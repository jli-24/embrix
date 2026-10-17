"""Finite KiCad source observation over supplied controlled bytes, never native I/O.

This profile records source structure only. It does not establish electrical
correctness, model geometry, editable semantics, or native round-trip support.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
from pathlib import PurePosixPath

from embedded_copilot.pcb.parser.kicad import _parse_s_expression, _tokenize
from embedded_copilot.pcb.parser.models import PCBParserLimits

from .contracts import fingerprint
from .kicad_project_coverage_models import (
    CoverageCountsV1,
    CoverageDependencyV1,
    CoverageEntryFilesV1,
    CoverageFileV1,
    CoverageIssueV1,
    CoverageObjectV1,
    CoverageRelationV1,
    CoverageSourceFile,
    CoverageTypeCountV1,
    CoverageUnknownV1,
    ProjectCoverageV1,
    aggregate_coverage_state,
    aggregate_dependency_state,
    relative_path,
)

# Finite parent/keyword and exact project JSON paths for the declared format
# profile. New keys are observed as UNKNOWN rather than swallowed as PROPERTY.
_SEXPR_CHILDREN = {'ROOT': frozenset({'footprint',
                    'fp_lib_table',
                    'kicad_pcb',
                    'kicad_sch',
                    'kicad_symbol_lib',
                    'sym_lib_table'}),
 'arc': frozenset({'start', 'mid', 'stroke', 'end', 'fill'}),
 'circle': frozenset({'fill', 'center', 'stroke', 'radius'}),
 'connect_pads': frozenset({'clearance'}),
 'effects': frozenset({'justify', 'font', 'hide'}),
 'fill': frozenset({'thermal_gap', 'type', 'thermal_bridge_width', 'color'}),
 'filled_polygon': frozenset({'layer', 'pts'}),
 'font': frozenset({'bold', 'size', 'italic', 'thickness'}),
 'footprint': frozenset({'at',
                         'attr',
                         'clearance',
                         'descr',
                         'fp_arc',
                         'fp_circle',
                         'fp_line',
                         'fp_text',
                         'generator',
                         'layer',
                         'model',
                         'pad',
                         'path',
                         'property',
                         'sheetfile',
                         'sheetname',
                         'tags',
                         'uuid',
                         'version'}),
 'fp_arc': frozenset({'start', 'mid', 'tstamp', 'stroke', 'end', 'layer', 'uuid'}),
 'fp_circle': frozenset({'stroke', 'end', 'layer', 'fill', 'center', 'uuid'}),
 'fp_lib_table': frozenset({'lib'}),
 'fp_line': frozenset({'start', 'tstamp', 'stroke', 'end', 'layer', 'uuid'}),
 'fp_text': frozenset({'effects', 'tstamp', 'at', 'layer', 'uuid'}),
 'general': frozenset({'legacy_teardrops', 'thickness'}),
 'gr_line': frozenset({'start', 'stroke', 'end', 'layer', 'uuid'}),
 'gr_poly': frozenset({'fill', 'width', 'pts'}),
 'gr_text': frozenset({'effects', 'layer', 'uuid', 'at'}),
 'hierarchical_label': frozenset({'shape', 'effects', 'at', 'uuid', 'fields_autoplaced'}),
 'instances': frozenset({'project'}),
 'junction': frozenset({'color', 'diameter', 'uuid', 'at'}),
 'kicad_pcb': frozenset({'footprint',
                         'general',
                         'generator',
                         'generator_version',
                         'gr_line',
                         'gr_text',
                         'layers',
                         'net',
                         'paper',
                         'segment',
                         'setup',
                         'title_block',
                         'version',
                         'via',
                         'zone'}),
 'kicad_sch': frozenset({'generator',
                         'generator_version',
                         'hierarchical_label',
                         'junction',
                         'label',
                         'lib_symbols',
                         'no_connect',
                         'paper',
                         'polyline',
                         'sheet',
                         'sheet_instances',
                         'symbol',
                         'text',
                         'title_block',
                         'uuid',
                         'version',
                         'wire'}),
 'kicad_symbol_lib': frozenset({'version', 'generator', 'symbol'}),
 'label': frozenset({'effects', 'fields_autoplaced', 'uuid', 'at'}),
 'layer': frozenset({'material', 'color', 'loss_tangent', 'type', 'epsilon_r', 'thickness'}),
 'layers': frozenset({'0',
                      '31',
                      '32',
                      '33',
                      '34',
                      '35',
                      '36',
                      '37',
                      '38',
                      '39',
                      '40',
                      '41',
                      '42',
                      '43',
                      '44',
                      '45',
                      '46',
                      '47',
                      '48',
                      '49'}),
 'lib': frozenset({'uri', 'name', 'type', 'descr', 'options'}),
 'lib_symbols': frozenset({'symbol'}),
 'model': frozenset({'scale', 'rotate', 'offset'}),
 'name': frozenset({'effects'}),
 'no_connect': frozenset({'uuid', 'at'}),
 'number': frozenset({'effects'}),
 'offset': frozenset({'xyz'}),
 'options': frozenset({'clearance', 'anchor'}),
 'pad': frozenset({'at',
                   'drill',
                   'layers',
                   'net',
                   'options',
                   'pinfunction',
                   'pintype',
                   'primitives',
                   'remove_unused_layers',
                   'roundrect_rratio',
                   'size',
                   'tstamp',
                   'uuid',
                   'zone_connect'}),
 'path': frozenset({'unit', 'page', 'reference'}),
 'pcbplotparams': frozenset({'creategerberjobfile',
                             'dashed_line_dash_ratio',
                             'dashed_line_gap_ratio',
                             'disableapertmacros',
                             'drillshape',
                             'dxfimperialunits',
                             'dxfpolygonmode',
                             'dxfusepcbnewfont',
                             'hpglpendiameter',
                             'hpglpennumber',
                             'hpglpenspeed',
                             'layerselection',
                             'mirror',
                             'mode',
                             'outputdirectory',
                             'outputformat',
                             'pdf_back_fp_property_popups',
                             'pdf_front_fp_property_popups',
                             'plot_on_all_layers_selection',
                             'plotfptext',
                             'plotframeref',
                             'plotinvisibletext',
                             'plotreference',
                             'plotvalue',
                             'psa4output',
                             'psnegative',
                             'scaleselection',
                             'sketchpadsonfab',
                             'subtractmaskfromsilk',
                             'svgprecision',
                             'useauxorigin',
                             'usegerberadvancedattributes',
                             'usegerberattributes',
                             'usegerberextensions',
                             'viasonmask'}),
 'pin': frozenset({'effects', 'number', 'length', 'at', 'name', 'uuid'}),
 'pin_names': frozenset({'offset'}),
 'polygon': frozenset({'pts'}),
 'polyline': frozenset({'fill', 'pts', 'stroke', 'uuid'}),
 'primitives': frozenset({'gr_poly'}),
 'project': frozenset({'path'}),
 'property': frozenset({'effects', 'tstamp', 'at', 'hide', 'unlocked', 'id', 'layer', 'uuid'}),
 'pts': frozenset({'xy'}),
 'rectangle': frozenset({'start', 'fill', 'stroke', 'end'}),
 'rotate': frozenset({'xyz'}),
 'scale': frozenset({'xyz'}),
 'segment': frozenset({'start', 'width', 'end', 'layer', 'net', 'uuid'}),
 'setup': frozenset({'allow_soldermask_bridges_in_footprints',
                     'aux_axis_origin',
                     'pad_to_mask_clearance',
                     'pcbplotparams',
                     'stackup'}),
 'sheet': frozenset({'pin', 'instances', 'stroke', 'at', 'fill', 'uuid', 'property', 'size'}),
 'sheet_instances': frozenset({'path'}),
 'stackup': frozenset({'copper_finish', 'dielectric_constraints', 'layer'}),
 'stroke': frozenset({'type', 'width'}),
 'sym_lib_table': frozenset({'lib'}),
 'symbol': frozenset({'arc',
                      'at',
                      'circle',
                      'dnp',
                      'exclude_from_sim',
                      'extends',
                      'fields_autoplaced',
                      'in_bom',
                      'instances',
                      'lib_id',
                      'mirror',
                      'on_board',
                      'pin',
                      'pin_names',
                      'pin_numbers',
                      'polyline',
                      'power',
                      'property',
                      'rectangle',
                      'symbol',
                      'unit',
                      'uuid'}),
 'text': frozenset({'effects', 'exclude_from_sim', 'uuid', 'at'}),
 'title_block': frozenset({'rev', 'company', 'date', 'title'}),
 'via': frozenset({'size', 'drill', 'at', 'net', 'uuid', 'layers'}),
 'wire': frozenset({'pts', 'stroke', 'uuid'}),
 'zone': frozenset({'connect_pads',
                    'fill',
                    'filled_areas_thickness',
                    'filled_polygon',
                    'hatch',
                    'layer',
                    'min_thickness',
                    'net',
                    'net_name',
                    'polygon',
                    'uuid'})}

_JSON_FIELDS = {'/project': frozenset({'board',
                        'boards',
                        'cvpcb',
                        'erc',
                        'legacy',
                        'libraries',
                        'meta',
                        'net_settings',
                        'pcbnew',
                        'schematic',
                        'sheets',
                        'text_variables'}),
 '/project/board': frozenset({'viewports', 'layer_presets', 'ipc2581', 'design_settings', '3dviewports'}),
 '/project/board/design_settings': frozenset({'defaults',
                                              'diff_pair_dimensions',
                                              'drc_exclusions',
                                              'meta',
                                              'rule_severities',
                                              'rule_severitieslegacy_courtyards_overlap',
                                              'rule_severitieslegacy_no_courtyard_defined',
                                              'rules',
                                              'teardrop_options',
                                              'teardrop_parameters',
                                              'track_widths',
                                              'tuning_pattern_settings',
                                              'via_dimensions',
                                              'zones_allow_external_fillets',
                                              'zones_use_no_outline'}),
 '/project/board/design_settings/defaults': frozenset({'apply_defaults_to_fp_fields',
                                                       'apply_defaults_to_fp_shapes',
                                                       'apply_defaults_to_fp_text',
                                                       'board_outline_line_width',
                                                       'copper_line_width',
                                                       'copper_text_italic',
                                                       'copper_text_size_h',
                                                       'copper_text_size_v',
                                                       'copper_text_thickness',
                                                       'copper_text_upright',
                                                       'courtyard_line_width',
                                                       'dimension_precision',
                                                       'dimension_units',
                                                       'dimensions',
                                                       'fab_line_width',
                                                       'fab_text_italic',
                                                       'fab_text_size_h',
                                                       'fab_text_size_v',
                                                       'fab_text_thickness',
                                                       'fab_text_upright',
                                                       'other_line_width',
                                                       'other_text_italic',
                                                       'other_text_size_h',
                                                       'other_text_size_v',
                                                       'other_text_thickness',
                                                       'other_text_upright',
                                                       'pads',
                                                       'silk_line_width',
                                                       'silk_text_italic',
                                                       'silk_text_size_h',
                                                       'silk_text_size_v',
                                                       'silk_text_thickness',
                                                       'silk_text_upright',
                                                       'zones'}),
 '/project/board/design_settings/defaults/dimensions': frozenset({'arrow_length',
                                                                  'extension_offset',
                                                                  'keep_text_aligned',
                                                                  'suppress_zeroes',
                                                                  'text_position',
                                                                  'units_format'}),
 '/project/board/design_settings/defaults/pads': frozenset({'drill', 'height', 'width'}),
 '/project/board/design_settings/defaults/zones': frozenset({'min_clearance', '45_degree_only'}),
 '/project/board/design_settings/diff_pair_dimensions/*': frozenset({'width', 'gap', 'via_gap'}),
 '/project/board/design_settings/meta': frozenset({'version'}),
 '/project/board/design_settings/rule_severities': frozenset({'annular_width',
                                                              'clearance',
                                                              'connection_width',
                                                              'copper_edge_clearance',
                                                              'copper_sliver',
                                                              'courtyards_overlap',
                                                              'diff_pair_gap_out_of_range',
                                                              'diff_pair_uncoupled_length_too_long',
                                                              'drill_out_of_range',
                                                              'duplicate_footprints',
                                                              'extra_footprint',
                                                              'footprint',
                                                              'footprint_symbol_mismatch',
                                                              'footprint_type_mismatch',
                                                              'hole_clearance',
                                                              'hole_near_hole',
                                                              'invalid_outline',
                                                              'isolated_copper',
                                                              'item_on_disabled_layer',
                                                              'items_not_allowed',
                                                              'length_out_of_range',
                                                              'lib_footprint_issues',
                                                              'lib_footprint_mismatch',
                                                              'malformed_courtyard',
                                                              'microvia_drill_out_of_range',
                                                              'missing_courtyard',
                                                              'missing_footprint',
                                                              'net_conflict',
                                                              'npth_inside_courtyard',
                                                              'overlapping_pads',
                                                              'padstack',
                                                              'pth_inside_courtyard',
                                                              'shorting_items',
                                                              'silk_edge_clearance',
                                                              'silk_over_copper',
                                                              'silk_overlap',
                                                              'skew_out_of_range',
                                                              'solder_mask_bridge',
                                                              'starved_thermal',
                                                              'text_height',
                                                              'text_thickness',
                                                              'through_hole_pad_without_hole',
                                                              'too_many_vias',
                                                              'track_dangling',
                                                              'track_width',
                                                              'tracks_crossing',
                                                              'unconnected_items',
                                                              'unresolved_variable',
                                                              'via_dangling',
                                                              'zones_intersect'}),
 '/project/board/design_settings/rules': frozenset({'allow_blind_buried_vias',
                                                    'allow_microvias',
                                                    'max_error',
                                                    'min_clearance',
                                                    'min_connection',
                                                    'min_copper_edge_clearance',
                                                    'min_hole_clearance',
                                                    'min_hole_to_hole',
                                                    'min_microvia_diameter',
                                                    'min_microvia_drill',
                                                    'min_resolved_spokes',
                                                    'min_silk_clearance',
                                                    'min_text_height',
                                                    'min_text_thickness',
                                                    'min_through_hole_diameter',
                                                    'min_track_width',
                                                    'min_via_annular_width',
                                                    'min_via_diameter',
                                                    'solder_mask_to_copper_clearance',
                                                    'use_height_for_length_calcs'}),
 '/project/board/design_settings/teardrop_options/*': frozenset({'td_onpadsmd',
                                                                 'td_onroundshapesonly',
                                                                 'td_ontrackend',
                                                                 'td_onviapad'}),
 '/project/board/design_settings/teardrop_parameters/*': frozenset({'td_allow_use_two_tracks',
                                                                    'td_curve_segcount',
                                                                    'td_height_ratio',
                                                                    'td_length_ratio',
                                                                    'td_maxheight',
                                                                    'td_maxlen',
                                                                    'td_on_pad_in_zone',
                                                                    'td_target_name',
                                                                    'td_width_to_size_filter_ratio'}),
 '/project/board/design_settings/tuning_pattern_settings': frozenset({'diff_pair_defaults',
                                                                      'diff_pair_skew_defaults',
                                                                      'single_track_defaults'}),
 '/project/board/design_settings/tuning_pattern_settings/diff_pair_defaults': frozenset({'corner_radius_percentage',
                                                                                         'corner_style',
                                                                                         'max_amplitude',
                                                                                         'min_amplitude',
                                                                                         'single_sided',
                                                                                         'spacing'}),
 '/project/board/design_settings/tuning_pattern_settings/diff_pair_skew_defaults': frozenset({'corner_radius_percentage',
                                                                                              'corner_style',
                                                                                              'max_amplitude',
                                                                                              'min_amplitude',
                                                                                              'single_sided',
                                                                                              'spacing'}),
 '/project/board/design_settings/tuning_pattern_settings/single_track_defaults': frozenset({'corner_radius_percentage',
                                                                                            'corner_style',
                                                                                            'max_amplitude',
                                                                                            'min_amplitude',
                                                                                            'single_sided',
                                                                                            'spacing'}),
 '/project/board/design_settings/via_dimensions/*': frozenset({'diameter', 'drill'}),
 '/project/board/ipc2581': frozenset({'distpn', 'mfg', 'mpn', 'dist', 'internal_id'}),
 '/project/cvpcb': frozenset({'equivalence_files'}),
 '/project/erc': frozenset({'pin_map', 'rule_severities', 'erc_exclusions', 'meta'}),
 '/project/erc/meta': frozenset({'version'}),
 '/project/erc/rule_severities': frozenset({'bus_definition_conflict',
                                            'bus_entry_needed',
                                            'bus_label_syntax',
                                            'bus_to_bus_conflict',
                                            'bus_to_net_conflict',
                                            'conflicting_netclasses',
                                            'different_unit_footprint',
                                            'different_unit_net',
                                            'duplicate_reference',
                                            'duplicate_sheet_names',
                                            'endpoint_off_grid',
                                            'extra_units',
                                            'global_label_dangling',
                                            'hier_label_mismatch',
                                            'label_dangling',
                                            'lib_symbol_issues',
                                            'missing_bidi_pin',
                                            'missing_input_pin',
                                            'missing_power_pin',
                                            'missing_unit',
                                            'multiple_net_names',
                                            'net_not_bus_member',
                                            'no_connect_connected',
                                            'no_connect_dangling',
                                            'pin_not_connected',
                                            'pin_not_driven',
                                            'pin_to_pin',
                                            'power_pin_not_driven',
                                            'similar_labels',
                                            'simulation_model_issue',
                                            'unannotated',
                                            'unit_value_mismatch',
                                            'unresolved_variable',
                                            'wire_dangling'}),
 '/project/legacy': frozenset({'common'}),
 '/project/legacy/common': frozenset({'NetDir'}),
 '/project/libraries': frozenset({'pinned_symbol_libs', 'pinned_footprint_libs'}),
 '/project/meta': frozenset({'version', 'filename'}),
 '/project/net_settings': frozenset({'classes',
                                     'meta',
                                     'net_colors',
                                     'netclass_assignments',
                                     'netclass_patterns'}),
 '/project/net_settings/classes/*': frozenset({'bus_width',
                                               'clearance',
                                               'diff_pair_gap',
                                               'diff_pair_via_gap',
                                               'diff_pair_width',
                                               'line_style',
                                               'microvia_diameter',
                                               'microvia_drill',
                                               'name',
                                               'pcb_color',
                                               'schematic_color',
                                               'track_width',
                                               'via_diameter',
                                               'via_drill',
                                               'wire_width'}),
 '/project/net_settings/meta': frozenset({'version'}),
 '/project/net_settings/netclass_patterns/*': frozenset({'netclass', 'pattern'}),
 '/project/pcbnew': frozenset({'page_layout_descr_file', 'last_paths'}),
 '/project/pcbnew/last_paths': frozenset({'gencad',
                                          'idf',
                                          'netlist',
                                          'plot',
                                          'pos_files',
                                          'specctra_dsn',
                                          'step',
                                          'svg',
                                          'vmrl',
                                          'vrml'}),
 '/project/schematic': frozenset({'annotate_start_num',
                                  'bom_fmt_presets',
                                  'bom_fmt_settings',
                                  'bom_presets',
                                  'bom_settings',
                                  'connection_grid_size',
                                  'drawing',
                                  'legacy_lib_dir',
                                  'legacy_lib_list',
                                  'meta',
                                  'net_format_name',
                                  'ngspice',
                                  'page_layout_descr_file',
                                  'plot_directory',
                                  'spice_adjust_passive_values',
                                  'spice_current_sheet_as_root',
                                  'spice_external_command',
                                  'spice_model_current_sheet_as_root',
                                  'spice_save_all_currents',
                                  'spice_save_all_dissipations',
                                  'spice_save_all_voltages',
                                  'subpart_first_id',
                                  'subpart_id_separator'}),
 '/project/schematic/bom_fmt_settings': frozenset({'field_delimiter',
                                                   'keep_line_breaks',
                                                   'keep_tabs',
                                                   'name',
                                                   'ref_delimiter',
                                                   'ref_range_delimiter',
                                                   'string_delimiter'}),
 '/project/schematic/bom_settings': frozenset({'exclude_dnp',
                                               'fields_ordered',
                                               'filter_string',
                                               'group_symbols',
                                               'name',
                                               'sort_asc',
                                               'sort_field'}),
 '/project/schematic/bom_settings/fields_ordered/*': frozenset({'show', 'name', 'label', 'group_by'}),
 '/project/schematic/drawing': frozenset({'dashed_lines_dash_length_ratio',
                                          'dashed_lines_gap_length_ratio',
                                          'default_bus_thickness',
                                          'default_junction_size',
                                          'default_line_thickness',
                                          'default_text_size',
                                          'default_wire_thickness',
                                          'field_names',
                                          'intersheets_ref_own_page',
                                          'intersheets_ref_prefix',
                                          'intersheets_ref_short',
                                          'intersheets_ref_show',
                                          'intersheets_ref_suffix',
                                          'junction_size_choice',
                                          'label_size_ratio',
                                          'operating_point_overlay_i_precision',
                                          'operating_point_overlay_i_range',
                                          'operating_point_overlay_v_precision',
                                          'operating_point_overlay_v_range',
                                          'overbar_offset_ratio',
                                          'pin_symbol_size',
                                          'text_offset_ratio'}),
 '/project/schematic/meta': frozenset({'version'}),
 '/project/schematic/ngspice': frozenset({'fix_include_paths',
                                          'fix_passive_vals',
                                          'meta',
                                          'model_mode',
                                          'workbook_filename'}),
 '/project/schematic/ngspice/meta': frozenset({'version'}),
 '/project/text_variables': frozenset()}

_ROOTS = {
    'PCB': ('kicad_pcb', frozenset({'20240108'})),
    'SCHEMATIC': ('kicad_sch', frozenset({'20231120'})),
    'SYMBOL_LIBRARY': ('kicad_symbol_lib', frozenset({'20220331'})),
    'FOOTPRINT_LIBRARY_MEMBER': ('footprint', frozenset({'20231007', '20220427'})),
    'LIBRARY_TABLE': (None, frozenset()),
}
_OBJECT_KINDS = {
    'pad': 'PAD', 'segment': 'TRACK_SEGMENT', 'via': 'VIA', 'zone': 'ZONE',
    'gr_line': 'BOARD_GRAPHIC', 'gr_text': 'BOARD_TEXT', 'sheet': 'SHEET',
    'wire': 'WIRE', 'junction': 'JUNCTION', 'no_connect': 'NO_CONNECT',
    'label': 'LABEL', 'hierarchical_label': 'LABEL', 'polyline': 'POLYLINE',
    'text': 'TEXT', 'model': 'MODEL_3D_REF', 'lib': 'LIBRARY_ENTRY',
}


def _role(path: str) -> str:
    suffix = PurePosixPath(path).suffix
    return {'.kicad_pro': 'PROJECT_SETTINGS', '.kicad_sch': 'SCHEMATIC',
            '.kicad_pcb': 'PCB', '.kicad_sym': 'SYMBOL_LIBRARY',
            '.kicad_mod': 'FOOTPRINT_LIBRARY_MEMBER', '.wrl': 'MODEL_3D'}.get(
                suffix, 'LIBRARY_TABLE' if PurePosixPath(path).name in {'fp-lib-table', 'sym-lib-table'} else 'OTHER_SOURCE')


def _direct(node, keyword):
    return tuple(c for c in node[1:] if isinstance(c, tuple) and c and c[0] == keyword)


def _value(node, keyword):
    children = _direct(node, keyword)
    if len(children) != 1 or len(children[0]) != 2 or type(children[0][1]) is not str:
        return None
    return children[0][1]


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('COVERAGE_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _project_json(data: bytes):
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_json_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NON_FINITE_JSON')))


def _checked_payloads(payloads: Mapping[str, bytes], expected_files):
    if not isinstance(payloads, Mapping) or not payloads or len(payloads) > 5000:
        raise ValueError('COVERAGE_PAYLOADS_INVALID')
    copied = {}
    total = 0
    for path, data in payloads.items():
        relative_path(path)
        if type(data) is not bytes or len(data) > 16_777_216:
            raise ValueError('COVERAGE_PAYLOADS_INVALID')
        copied[path] = data
        total += len(data)
    if total > 64 * 1024 * 1024 or len({p.casefold() for p in copied}) != len(copied):
        raise ValueError('COVERAGE_PAYLOADS_INVALID')
    if expected_files is not None:
        if type(expected_files) is not tuple or any(type(f) is not CoverageSourceFile for f in expected_files):
            raise ValueError('COVERAGE_MANIFEST_INVALID')
        expected = {f.relative_path: f for f in expected_files}
        if len(expected) != len(expected_files) or expected.keys() != copied.keys():
            raise ValueError('SOURCE_CHANGED')
        for path, data in copied.items():
            if len(data) != expected[path].byte_size or sha256(data).hexdigest() != expected[path].sha256:
                raise ValueError('SOURCE_CHANGED')
    return copied


def parse_kicad_project_coverage(*, native_entry_relative_path: str,
        source_set_fingerprint: str, payloads: Mapping[str, bytes],
        expected_files: tuple[CoverageSourceFile, ...] | None = None) -> ProjectCoverageV1 | None:
    """Return a source envelope only for meta-v1 projects with all three entries.

    Unmatched legacy entry profiles omit the envelope. Once admitted, unsupported
    member versions/grammar remain explicit whole-file UNKNOWN observations.
    The caller owns source-set canonical identity and controlled scope reads.
    """
    relative_path(native_entry_relative_path)
    fingerprint(source_set_fingerprint)
    data = _checked_payloads(payloads, expected_files)
    if not native_entry_relative_path.endswith('.kicad_pro'):
        return None
    base = native_entry_relative_path.removesuffix('.kicad_pro')
    sch, pcb = base + '.kicad_sch', base + '.kicad_pcb'
    if not {native_entry_relative_path, sch, pcb} <= data.keys():
        return None
    try:
        entry = _project_json(data[native_entry_relative_path])
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get('meta'), dict) or type(entry['meta'].get('version')) is not int or entry['meta']['version'] != 1:
        return None

    files = []; objects = []; unknowns = []; relations = []; dependencies = []; issues = []
    nodes = {}; definitions = {}; table_entries = {}; net_targets = {}

    def issue(code, path, locator=None):
        issues.append(CoverageIssueV1(code=code, source_file=path, locator=locator,
                      blocks_complete=code not in {'DEPENDENCY_EXTERNAL', 'DEPENDENCY_MISSING'}))

    def add_object(path, locator, parent, kind, keyword, values, children, native_id=None, reason=None):
        objects.append(CoverageObjectV1(source_file=path, locator=locator,
            parent_locator=parent, native_kind=kind, native_id=native_id,
            keyword=keyword, values=tuple(values), child_locators=tuple(children), source_span=None))
        if reason is not None:
            unknowns.append(CoverageUnknownV1(source_file=path, locator=locator,
                native_keyword=keyword, source_file_sha256=sha256(data[path]).hexdigest(),
                source_span=None, reason=reason))
            issue('MODEL_GEOMETRY_OPAQUE' if reason == 'OPAQUE_GEOMETRY' else
                  'PARSE_FAILURE' if reason == 'PARSE_FAILURE' else
                  'FORMAT_UNSUPPORTED' if reason == 'UNKNOWN_FORMAT' else 'OBJECT_UNKNOWN', path, locator)
        if parent is not None:
            relations.append(CoverageRelationV1(relation_type='CONTAINS', source_file=path,
                source_locator=parent, target_file=path, target_locator=locator,
                target_reference=locator, state='RESOLVED_IN_SOURCE'))

    def walk_sexpr(path, node, locator, parent, parent_keyword, role):
        if not node or type(node[0]) is not str:
            raise ValueError('COVERAGE_GRAMMAR_INVALID')
        keyword = node[0]
        child_specs = []; seen = Counter()
        for child in node[1:]:
            if isinstance(child, tuple):
                if not child or type(child[0]) is not str:
                    raise ValueError('COVERAGE_GRAMMAR_INVALID')
                name = child[0]
                child_specs.append((child, f'{locator}/{name}[{seen[name]}]'))
                seen[name] += 1
        recognized = keyword in _SEXPR_CHILDREN.get(parent_keyword, ())
        kind = 'PROPERTY'
        if keyword == 'footprint':
            kind = 'FOOTPRINT' if role == 'PCB' else 'FOOTPRINT_DEFINITION'
        elif keyword == 'symbol':
            kind = 'SCHEMATIC_SYMBOL' if parent_keyword == 'kicad_sch' else 'SYMBOL_DEFINITION'
        elif keyword == 'net' and parent_keyword == 'kicad_pcb':
            kind = 'PCB_NET'
        elif parent_keyword == 'kicad_sch' or keyword in {'pad', 'segment', 'via', 'zone', 'model', 'lib', 'gr_line', 'gr_text'}:
            kind = _OBJECT_KINDS.get(keyword, kind)
        if not recognized:
            kind = 'UNKNOWN'
        if len(_direct(node, 'uuid')) + len(_direct(node, 'tstamp')) > 1:
            raise ValueError('COVERAGE_DUPLICATE_NATIVE_ID')
        native_id = _value(node, 'uuid') or _value(node, 'tstamp')
        # lib/cache names are scoped structural values, not globally unique UUIDs.
        if kind == 'PCB_NET' and len(node) >= 2 and type(node[1]) is str:
            native_id = node[1]
        add_object(path, locator, parent, kind, keyword,
            (v for v in node[1:] if type(v) is str),
            (loc for _, loc in child_specs), native_id,
            'UNKNOWN_KEYWORD' if kind == 'UNKNOWN' else None)
        nodes[(path, locator)] = node
        if kind == 'SYMBOL_DEFINITION' and parent_keyword in {'lib_symbols', 'kicad_symbol_lib'} and len(node) >= 2 and type(node[1]) is str:
            definitions[(path, node[1])] = locator
        if kind == 'PCB_NET':
            if (path, native_id) in net_targets:
                raise ValueError('COVERAGE_DUPLICATE_NATIVE_ID')
            net_targets[(path, native_id)] = locator
        if kind == 'LIBRARY_ENTRY':
            name = _value(node, 'name'); uri = _value(node, 'uri')
            if name is not None and uri is not None:
                key = (str(PurePosixPath(path).parent), 'FOOTPRINT_LIBRARY' if PurePosixPath(path).name == 'fp-lib-table' else 'SYMBOL_LIBRARY', name)
                if key in table_entries:
                    raise ValueError('COVERAGE_DUPLICATE_LIBRARY_ENTRY')
                table_entries[key] = (path, locator, uri)
        for child, child_locator in child_specs:
            walk_sexpr(path, child, child_locator, locator, keyword, role)

    def walk_json(path, value, locator, parent, schema_path, keyword, recognized=True, root=False, depth=0):
        if depth > 128:
            raise ValueError('COVERAGE_GRAMMAR_INVALID')
        specs = []; values = []
        if isinstance(value, dict):
            allowed = _JSON_FIELDS.get(schema_path, ())
            for key in sorted(value):
                # JSON pointer escaping keeps a raw key unambiguous in locators.
                escaped = key.replace('~', '~0').replace('/', '~1')
                specs.append((value[key], f'{locator}/{escaped}', schema_path+'/'+key, key, key in allowed))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                specs.append((item, f'{locator}/[{index}]', schema_path+'/*', 'item', recognized))
        else:
            values = [value if type(value) is str else json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))]
        kind = 'PROJECT_SETTINGS' if root else 'PROPERTY' if recognized else 'UNKNOWN'
        add_object(path, locator, parent, kind, keyword, values, (s[1] for s in specs),
                   reason='UNKNOWN_KEYWORD' if kind == 'UNKNOWN' else None)
        for child, loc, child_schema, key, known in specs:
            walk_json(path, child, loc, locator, child_schema, key, known and recognized, depth=depth+1)

    for path in sorted(data):
        content = data[path]; role = _role(path); marker = None; status = 'PARSED'
        start = (len(objects), len(unknowns), len(relations), len(issues))
        try:
            if role == 'PROJECT_SETTINGS':
                project = _project_json(content)
                meta = project.get('meta') if isinstance(project, dict) else None
                marker = str(meta.get('version')) if isinstance(meta, dict) else None
                if marker != '1':
                    raise LookupError('unsupported project format')
                walk_json(path, project, '/project', None, '/project', 'project', root=True)
            elif role in _ROOTS:
                text = content.decode('utf-8-sig')
                if '\x00' in text:
                    raise ValueError('COVERAGE_GRAMMAR_INVALID')
                # The reused lexical parser does not distinguish quoted single
                # parens from structural tokens. Reject this finite ambiguity
                # explicitly instead of emitting a corrupted decoded tree.
                for quoted in re.findall(r'"(?:\\.|[^"\\])*"', text):
                    if _tokenize(quoted, PCBParserLimits()) in [['('], [')']]:
                        raise ValueError('COVERAGE_QUOTED_DELIMITER_UNSUPPORTED')
                tree = _parse_s_expression(text, PCBParserLimits())
                root_keyword, versions = _ROOTS[role]
                marker = _value(tree, 'version')
                if role == 'LIBRARY_TABLE':
                    expected_root = 'fp_lib_table' if PurePosixPath(path).name == 'fp-lib-table' else 'sym_lib_table'
                    if not tree or tree[0] != expected_root or _direct(tree, 'version'):
                        raise LookupError('unsupported table format')
                elif not tree or tree[0] != root_keyword or marker not in versions:
                    raise LookupError('unsupported source format')
                walk_sexpr(path, tree, '/'+tree[0], None, 'ROOT', role)
            elif role == 'MODEL_3D' and content.split(b'\n', 1)[0].removesuffix(b'\r') == b'#VRML V2.0 utf8':
                marker = '#VRML V2.0 utf8'; status = 'OPAQUE'
                add_object(path, '/file', None, 'UNKNOWN', 'wrl', (), (), reason='OPAQUE_GEOMETRY')
            else:
                raise LookupError('unsupported source format')
        except (LookupError, ValueError, UnicodeError, RecursionError) as error:
            if str(error).startswith('COVERAGE_DUPLICATE'):
                raise
            # Never keep a half-parsed file or an inferred empty dependency set.
            for collection, length in zip((objects, unknowns, relations, issues), start):
                del collection[length:]
            for key in tuple(nodes):
                if key[0] == path:
                    del nodes[key]
            for key in tuple(definitions):
                if key[0] == path:
                    del definitions[key]
            for key in tuple(net_targets):
                if key[0] == path:
                    del net_targets[key]
            for key, item in tuple(table_entries.items()):
                if item[0] == path:
                    del table_entries[key]
            status = 'UNSUPPORTED' if isinstance(error, LookupError) else 'FAILED'
            add_object(path, '/file', None, 'UNKNOWN', 'file', (), (),
                       reason='UNKNOWN_FORMAT' if status == 'UNSUPPORTED' else 'PARSE_FAILURE')
        files.append(CoverageFileV1(relative_path=path, byte_size=len(content), sha256=sha256(content).hexdigest(),
                                   role=role, format_marker=marker, parse_status=status))

    scope = PurePosixPath(native_entry_relative_path).parent
    def resolve(reference, source_path, *, project_relative=False):
        if reference.startswith('${KIPRJMOD}/'):
            tail = reference[len('${KIPRJMOD}/'):]; base_path = scope
        elif project_relative:
            tail = reference; base_path = PurePosixPath(source_path).parent
        else:
            return None, 'EXTERNAL_UNVERIFIED'
        try:
            relative_path(tail)
            resolved = str(base_path / tail)
            relative_path(resolved)
            if scope != PurePosixPath('.') and not PurePosixPath(resolved).is_relative_to(scope):
                raise ValueError('outside controlled source scope')
        except ValueError:
            return None, 'UNSUPPORTED'
        return resolved, 'RESOLVED_IN_SOURCE' if resolved in data else 'MISSING'

    def add_dependency(path, locator, reference, kind, resolved, state, relation_type, target_locator=None):
        dependencies.append(CoverageDependencyV1(source_file=path, source_locator=locator,
            reference=reference, kind=kind, resolved_relative_path=resolved, state=state))
        relations.append(CoverageRelationV1(relation_type=relation_type, source_file=path,
            source_locator=locator, target_file=resolved if state == 'RESOLVED_IN_SOURCE' and resolved in data else None,
            target_locator=target_locator if state == 'RESOLVED_IN_SOURCE' else None,
            target_reference=reference, state=state))
        if state != 'RESOLVED_IN_SOURCE':
            issue('DEPENDENCY_EXTERNAL' if state == 'EXTERNAL_UNVERIFIED' else
                  'DEPENDENCY_MISSING' if state == 'MISSING' else 'RELATION_UNRESOLVED', path, locator)

    for obj in tuple(objects):
        node = nodes.get((obj.source_file, obj.locator))
        if node is None:
            continue
        if obj.native_kind == 'LIBRARY_ENTRY':
            uri = _value(node, 'uri'); name = _value(node, 'name')
            kind = 'FOOTPRINT_LIBRARY' if PurePosixPath(obj.source_file).name == 'fp-lib-table' else 'SYMBOL_LIBRARY'
            if uri is None or name is None or _value(node, 'type') != 'KiCad':
                add_dependency(obj.source_file, obj.locator, uri or '', kind, None, 'UNSUPPORTED', 'USES_LIBRARY')
            else:
                resolved, state = resolve(uri, obj.source_file)
                # A .pretty directory is represented by its supplied member files.
                if state == 'MISSING' and kind == 'FOOTPRINT_LIBRARY' and any(p.startswith(resolved+'/') for p in data):
                    state = 'RESOLVED_IN_SOURCE'
                add_dependency(obj.source_file, obj.locator, uri, kind, resolved, state, 'USES_LIBRARY')
        elif obj.native_kind == 'MODEL_3D_REF':
            reference = obj.values[0] if len(obj.values) == 1 else ''
            resolved, state = resolve(reference, obj.source_file) if reference else (None, 'UNSUPPORTED')
            add_dependency(obj.source_file, obj.locator, reference, 'MODEL_3D', resolved, state, 'USES_MODEL', '/file')
        elif obj.native_kind == 'SHEET':
            refs = [c[2] for c in _direct(node, 'property') if len(c) >= 3 and c[1] == 'Sheetfile' and type(c[2]) is str]
            reference = refs[0] if len(refs) == 1 else ''
            resolved, state = resolve(reference, obj.source_file, project_relative=True) if reference else (None, 'UNSUPPORTED')
            if state == 'RESOLVED_IN_SOURCE' and (resolved, '/kicad_sch') not in nodes:
                state = 'UNSUPPORTED'
            add_dependency(obj.source_file, obj.locator, reference, 'SCHEMATIC_SHEET', resolved, state, 'SHEET_PATH', '/kicad_sch')
        elif (obj.native_kind in {'FOOTPRINT', 'SCHEMATIC_SYMBOL'} or
                (obj.keyword == 'property' and len(obj.values) >= 2 and obj.values[0] == 'Footprint' and obj.values[1])):
            reference = (obj.values[1] if obj.keyword == 'property' else
                obj.values[0] if obj.native_kind == 'FOOTPRINT' and obj.values else _value(node, 'lib_id'))
            kind = 'SYMBOL_LIBRARY' if obj.native_kind == 'SCHEMATIC_SYMBOL' else 'FOOTPRINT_LIBRARY'
            resolved = target = None; state = 'UNSUPPORTED'
            if reference is not None and reference.count(':') == 1:
                family, member = reference.split(':')
                entry_item = table_entries.get((str(scope), kind, family))
                if entry_item is None:
                    state = 'EXTERNAL_UNVERIFIED'
                else:
                    entry_file, entry_locator, uri = entry_item
                    if _value(nodes[(entry_file, entry_locator)], 'type') == 'KiCad':
                        resolved, state = resolve(uri, obj.source_file)
                        if kind == 'FOOTPRINT_LIBRARY' and resolved is not None:
                            resolved = resolved+'/'+member+'.kicad_mod'
                            state = 'RESOLVED_IN_SOURCE' if resolved in data else 'MISSING'
                            target = '/footprint'
                            definition = nodes.get((resolved, target))
                            if state == 'RESOLVED_IN_SOURCE' and (definition is None or len(definition) < 2 or definition[1] != member):
                                state = 'UNSUPPORTED' if definition is None else 'MISSING'
                        elif state == 'RESOLVED_IN_SOURCE':
                            target = definitions.get((resolved, member))
                            if target is None:
                                state = 'MISSING'
            add_dependency(obj.source_file, obj.locator, reference or '', kind, resolved, state, 'USES_LIBRARY', target)
            # Cached symbol source is an explicit local instance relationship,
            # independent of whether the original library member is available.
            if obj.native_kind == 'SCHEMATIC_SYMBOL':
                cached = definitions.get((obj.source_file, reference))
                if cached is not None:
                    relations.append(CoverageRelationV1(relation_type='INSTANCE_OF', source_file=obj.source_file,
                        source_locator=obj.locator, target_file=obj.source_file, target_locator=cached,
                        target_reference=reference, state='RESOLVED_IN_SOURCE'))
                else:
                    issue('RELATION_UNRESOLVED', obj.source_file, obj.locator)
        if obj.native_kind == 'SYMBOL_DEFINITION' and _direct(node, 'extends'):
            reference = _value(node, 'extends')
            target = definitions.get((obj.source_file, reference))
            relations.append(CoverageRelationV1(relation_type='INSTANCE_OF', source_file=obj.source_file,
                source_locator=obj.locator, target_file=obj.source_file if target else None,
                target_locator=target, target_reference=reference or '',
                state='RESOLVED_IN_SOURCE' if target else 'MISSING' if reference else 'UNSUPPORTED'))
            if target is None:
                issue('RELATION_UNRESOLVED', obj.source_file, obj.locator)
        if obj.native_kind in {'PAD', 'TRACK_SEGMENT', 'VIA', 'ZONE'} and _role(obj.source_file) == 'PCB':
            refs = _direct(node, 'net')
            if len(refs) > 1 or (refs and (len(refs[0]) < 2 or type(refs[0][1]) is not str)):
                issue('RELATION_UNRESOLVED', obj.source_file, obj.locator)
            elif refs:
                reference = refs[0][1]; target = net_targets.get((obj.source_file, reference))
                relations.append(CoverageRelationV1(relation_type='CONNECTS_NET', source_file=obj.source_file,
                    source_locator=obj.locator, target_file=obj.source_file if target else None,
                    target_locator=target, target_reference=reference,
                    state='RESOLVED_IN_SOURCE' if target else 'MISSING'))
                if target is None:
                    issue('RELATION_UNRESOLVED', obj.source_file, obj.locator)

    objects.sort(key=lambda o: (o.source_file, o.locator))
    unknowns.sort(key=lambda o: (o.source_file, o.locator))
    relations.sort(key=lambda r: (r.source_file, r.source_locator, r.relation_type, r.target_file or '', r.target_locator or '', r.target_reference))
    dependencies.sort(key=lambda d: (d.source_file, d.source_locator, d.kind, d.reference))
    issues = sorted({(i.source_file, i.locator or '', i.code): i for i in issues}.values(),
                    key=lambda i: (i.source_file, i.locator or '', i.code))
    counts = Counter(o.native_kind for o in objects)
    return ProjectCoverageV1(schema='kicad-project-source-coverage-v1', profile_id='KICAD_PROJECT_SOURCE_20240108_V1',
        source_set_fingerprint=source_set_fingerprint,
        entry_files=CoverageEntryFilesV1(project=native_entry_relative_path, schematic_root=sch, board=pcb),
        files=tuple(files), objects=tuple(objects), relations=tuple(relations), dependencies=tuple(dependencies), unknowns=tuple(unknowns),
        counts=CoverageCountsV1(objects_total=len(objects), recognized_total=len(objects)-len(unknowns), unknown_total=len(unknowns),
            by_kind=tuple(CoverageTypeCountV1(native_kind=k, count=counts[k]) for k in sorted(counts))),
        coverage_state=aggregate_coverage_state(files, unknowns, issues),
        dependency_state=aggregate_dependency_state(dependencies, files, unknowns), issues=tuple(issues))


def validate_project_coverage_source(coverage: ProjectCoverageV1, payloads: Mapping[str, bytes],
        expected_files: tuple[CoverageSourceFile, ...] | None = None) -> ProjectCoverageV1:
    """Revalidate identities, raw spans and deterministic parsing at a boundary."""
    if type(coverage) is not ProjectCoverageV1:
        raise ValueError('COVERAGE_TYPE_INVALID')
    checked = ProjectCoverageV1.model_validate(coverage.model_dump(mode='python'))
    data = _checked_payloads(payloads, expected_files)
    if set(data) != {f.relative_path for f in checked.files}:
        raise ValueError('SOURCE_CHANGED')
    for file in checked.files:
        raw = data[file.relative_path]
        if len(raw) != file.byte_size or sha256(raw).hexdigest() != file.sha256:
            raise ValueError('SOURCE_CHANGED')
    for obj in checked.objects:
        if obj.source_span is not None:
            span = obj.source_span
            raw = data[obj.source_file][span.byte_start:span.byte_start+span.byte_length]
            if len(raw) != span.byte_length or sha256(raw).hexdigest() != span.sha256:
                raise ValueError('COVERAGE_SPAN_INVALID')
    reparsed = parse_kicad_project_coverage(native_entry_relative_path=checked.entry_files.project,
        source_set_fingerprint=checked.source_set_fingerprint, payloads=data, expected_files=expected_files)
    if reparsed is None or reparsed.fingerprint != checked.fingerprint:
        raise ValueError('COVERAGE_SOURCE_BINDING_INVALID')
    return checked