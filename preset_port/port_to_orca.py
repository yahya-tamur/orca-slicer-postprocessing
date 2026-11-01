"""Port PrusaSlicer 3.0 (CORE One 0.4 HF) printer/filament/print settings to OrcaSlicer user presets.

Templates: Orca's own CORE One port (flattened copies in user/default/*/base).
Values:    fully resolved PrusaSlicer 3.0 config from the last saved project (ps3_resolved.json),
           which matches the user's current "polymaker polylite pla pro" preset.
"""
import copy, json, os, re, sys, time

ORCA = os.path.join(os.environ["APPDATA"], "OrcaSlicer", "user", "default")
OUT = sys.argv[1] if len(sys.argv) > 1 else ORCA

PRINTER = "Prusa CORE One+ Gen 2 0.4 HF"
FILAMENT = "Polymaker PolyLite PLA Pro @CORE One+ Gen 2"
PROCESS = "0.20mm BALANCED @CORE One+ Gen 2 0.4 HF"
VERSION = "2.4.0.3"

def _existing_printer_address():
    """The printer's PrusaLink address: PRINTER_IP env var, else print_host_webui of the current preset."""
    if os.environ.get("PRINTER_IP"):
        return os.environ["PRINTER_IP"]
    path = os.path.join(ORCA, "machine", PRINTER + ".json")
    if os.path.exists(path):
        webui = json.load(open(path, encoding="utf-8")).get("print_host_webui", "")
        return webui.split("://", 1)[-1].rstrip("/")
    return ""


PRINTER_IP = _existing_printer_address()  # kept out of the repository; used by the helper's PrusaLink button

ps = json.load(open("ps3_resolved.json"))["configuration"]
P = ps["printer_settings"]
F = {k: v[0] for k, v in ps["filament_settings"].items()}
S = ps["print_settings"]


def load(kind, name):
    return json.load(open(os.path.join(ORCA, kind, "base", name + ".json"), encoding="utf-8"))


def s(v):
    """Orca stores every scalar as a string."""
    if isinstance(v, dict):  # PrusaSlicer 3 FloatOrPercent
        return f"{v['value']:g}%" if v["is_percent"] else f"{v['value']:g}"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def pair(v):
    return [s(x) for x in v]


# ---------------------------------------------------------------- machine
m = load("machine", "Prusa Prusa CORE One 0.4 nozzle")
m.update({
    "name": PRINTER,
    "printer_settings_id": PRINTER,
    "inherits": "",
    "from": "User",
    "version": VERSION,
    "printer_model": "Prusa CORE One",
    "printer_variant": "0.4",
    "nozzle_diameter": ["0.4"],
    "printable_area": [f"{x:g}x{y:g}" for x, y in P["bed_shape"]],
    "printable_height": s(P["max_print_height"]),
    "extruder_clearance_radius": s(P["extruder_clearance_radius"]),
    "extruder_clearance_height_to_rod": s(P["extruder_clearance_height"]),
    "gcode_flavor": P["gcode_flavor"],
    "use_relative_e_distances": s(P["use_relative_e_distances"]),
    "use_firmware_retraction": s(P["use_firmware_retraction"]),
    "silent_mode": s(P["silent_mode"]),
    "emit_machine_limits_to_gcode": "1" if P["machine_limits_usage"] == "emit_to_gcode" else "0",
    "machine_max_acceleration_e": pair(P["machine_max_acceleration_e"]),
    "machine_max_acceleration_extruding": pair(P["machine_max_acceleration_extruding"]),
    "machine_max_acceleration_retracting": pair(P["machine_max_acceleration_retracting"]),
    "machine_max_acceleration_travel": pair(P["machine_max_acceleration_travel"]),
    "machine_max_acceleration_x": pair(P["machine_max_acceleration_x"]),
    "machine_max_acceleration_y": pair(P["machine_max_acceleration_y"]),
    "machine_max_acceleration_z": pair(P["machine_max_acceleration_z"]),
    "machine_max_speed_e": pair(P["machine_max_feedrate_e"]),
    "machine_max_speed_x": pair(P["machine_max_feedrate_x"]),
    "machine_max_speed_y": pair(P["machine_max_feedrate_y"]),
    "machine_max_speed_z": pair(P["machine_max_feedrate_z"]),
    "machine_max_jerk_e": pair(P["machine_max_jerk_e"]),
    "machine_max_jerk_x": pair(P["machine_max_jerk_x"]),
    "machine_max_jerk_y": pair(P["machine_max_jerk_y"]),
    "machine_max_jerk_z": pair(P["machine_max_jerk_z"]),
    "machine_max_junction_deviation": [s(P["machine_max_junction_deviation"][0])],
    "machine_min_extruding_rate": pair(P["machine_min_extruding_rate"]),
    "machine_min_travel_rate": pair(P["machine_min_travel_rate"]),
    "cooling_tube_length": s(P["cooling_tube_length"]),
    "cooling_tube_retraction": s(P["cooling_tube_retraction"]),
    "parking_pos_retraction": s(P["parking_pos_retraction"]),
    "extra_loading_move": s(P["extra_loading_move"]),
    "high_current_on_filament_swap": s(P["high_current_on_filament_swap"]),
    "z_offset": s(P["z_offset"]),
    "thumbnails": P["thumbnails"].replace(",", ", "),
    "machine_pause_gcode": P["pause_print_gcode"],
    "change_filament_gcode": P["color_change_gcode"],
    "before_layer_change_gcode": P["before_layer_gcode"],
    "layer_change_gcode": P["layer_gcode"].rstrip("\n"),
    # PrusaSlicer 3.0 end gcode uses only variables Orca also has -> verbatim.
    "machine_end_gcode": P["end_gcode"],
    # Retraction / travel lift: in PrusaSlicer 3 these live in the print profile.
    "retraction_length": [s(S["retract_length"])],
    "retraction_speed": [s(S["retract_speed"])],
    "deretraction_speed": [s(S["deretract_speed"])],
    "retraction_minimum_travel": [s(S["retract_before_travel"])],
    "retract_before_wipe": [s(S["retract_before_wipe"])],
    "retract_when_changing_layer": [s(S["retract_layer_change"])],
    "retract_length_toolchange": [s(S["retract_length_toolchange"])],
    "retract_restart_extra": [s(S["retract_restart_extra"])],
    "retract_restart_extra_toolchange": [s(S["retract_restart_extra_toolchange"])],
    "retract_lift_above": [s(S["retract_lift_above"])],
    "retract_lift_below": [s(S["retract_lift_below"])],
    "z_hop": [s(S["retract_lift"])],
    "z_hop_types": ["Slope Lift" if S["travel_ramping_lift"] else "Normal Lift"],
    "travel_slope": [s(S["travel_slope"])],
    "wipe": [s(S["wipe"])],
    "max_layer_height": [s(S["max_layer_height"])],
    "min_layer_height": [s(S["min_layer_height"])],
    "default_print_profile": PROCESS,
    "default_filament_profile": [FILAMENT],
    "printer_notes": m["printer_notes"],  # keeps PRINTER_MODEL_COREONE / HF_NOZZLE / PG keywords
    # Orca 2.5 only offers ZV/Disable for Marlin flavors and shows the template's "Default" as a
    # changed ZV. Nothing is emitted (input_shaping_emit = 0); the firmware's own shaper is used.
    "input_shaping_type": "ZV",
    # Print button -> USB helper (helper/usb_print_host.py, a stand-in OctoPrint host on localhost).
    # For PrusaLink instead: host_type "prusalink", print_host PRINTER_IP, authorization "user", user "maker"
    # (digest auth; the password is entered in Orca, never stored here).
    "host_type": "octoprint",
    "print_host": "127.0.0.1:51200",
    "print_host_webui": f"http://{PRINTER_IP}" if PRINTER_IP else "",
    "printhost_authorization_type": "key",
    "printhost_apikey": "usb-helper",
    "printhost_user": "maker",
})

# Keep the PrusaLink password the user saved in Orca when re-porting.
existing = os.path.join(OUT, "machine", PRINTER + ".json")
if os.path.exists(existing):
    old = json.load(open(existing, encoding="utf-8"))
    for k in ("printhost_password",):
        if old.get(k):
            m[k] = old[k]

# Start gcode: PrusaSlicer 3.0's sequence, with the four PS-only variables swapped for Orca equivalents
# (same substitutions Orca's own CORE One port uses).
start = P["start_gcode"]
for old, new in [
    ("A{(filament_abrasive[0] ? 1 : 0)}", "A{(printer_notes=~/.*ABRASIVE_NOZZLE.*/ ? 1 : 0)}"),
    ("F{(nozzle_high_flow[0] ? 1 : 0)}", "F{(printer_notes=~/.*HF_NOZZLE.*/ ? 1 : 0)}"),
    ("{if is_nil(idle_temperature[initial_tool])}100{else}{idle_temperature[initial_tool]}{endif}",
     "{if idle_temperature[initial_tool] == 0}100{else}{idle_temperature[initial_tool]}{endif}"),
]:
    assert old in start, old
    start = start.replace(old, new)
# Orca has no chamber_minimal_temperature; Orca's port treats chamber_temperature > 35 as "preheat chamber".
start = start.replace("{if chamber_minimal_temperature[initial_tool]!=0}", "{if chamber_temperature[initial_tool] > 35}")
start = start.replace("M191 S{chamber_minimal_temperature[initial_tool]}", "M191 S{chamber_temperature[initial_tool]}")
assert "chamber_minimal_temperature" not in start and "nozzle_high_flow" not in start
m["machine_start_gcode"] = start

# ---------------------------------------------------------------- filament
f = load("filament", "Prusa Generic PETG @Prusa Prusa CORE One 0.4 nozzle")
bed, bed0 = s(F["bed_temperature"]), s(F["first_layer_bed_temperature"])
f.update({
    "name": FILAMENT,
    "filament_settings_id": [FILAMENT],
    "filament_id": "",
    "inherits": "",
    "from": "User",
    "version": VERSION,
    "compatible_printers": [PRINTER],
    "compatible_printers_condition": "",
    "filament_type": [F["filament_type"]],
    "filament_vendor": ["Polymaker"],
    "default_filament_colour": [ps["project_settings"]["extruder_colour"][0]],
    "filament_diameter": [s(F["filament_diameter"])],
    "filament_density": [s(F["filament_density"])],
    "filament_cost": [s(F["filament_cost"])],
    "filament_flow_ratio": [s(F["extrusion_multiplier"])],
    "filament_max_volumetric_speed": [s(F["filament_max_volumetric_speed"])],
    "nozzle_temperature": [s(F["temperature"])],
    "nozzle_temperature_initial_layer": [s(F["first_layer_temperature"])],
    "nozzle_temperature_range_low": ["190"],
    "nozzle_temperature_range_high": ["230"],
    "idle_temperature": [s(F["idle_temperature"])],
    "chamber_temperature": [s(F["chamber_temperature"])],
    "chamber_minimal_temperature": [s(F["chamber_minimal_temperature"])],
    "temperature_vitrification": ["55"],
    # Like Orca's own Prusa port: Orca's PA option stays off, Prusa firmware gets M572 from filament gcode.
    "enable_pressure_advance": ["0"],
    "pressure_advance": [s(F["pressure_advance_value"])],
    "fan_min_speed": [s(F["min_fan_speed"])],
    "fan_max_speed": [s(F["max_fan_speed"])],
    "fan_cooling_layer_time": [s(F["fan_below_layer_time"])],
    "reduce_fan_stop_start_freq": [s(F["fan_always_on"])],
    "slow_down_for_layer_cooling": [s(F["cooling"])],
    "slow_down_layer_time": [s(F["slowdown_below_layer_time"])],
    "slow_down_min_speed": [s(F["min_print_speed"])],
    "close_fan_the_first_x_layers": [s(F["disable_fan_first_layers"])],
    "full_fan_speed_layer": [s(F["full_fan_speed_layer"])],
    "enable_overhang_bridge_fan": ["1"],
    "overhang_fan_speed": [s(F["bridge_fan_speed"])],
    "overhang_fan_threshold": ["25%"],
    "filament_start_gcode": [
        (f"M572 S{s(F['pressure_advance_value'])} ; pressure advance\n" if F["pressure_advance"] == "enabled" else "")
        + F["start_filament_gcode"]],
    "filament_end_gcode": [F["end_filament_gcode"]],
    "filament_notes": [F["filament_notes"]],
    "filament_soluble": [s(F["filament_soluble"])],
    "filament_minimal_purge_on_wipe_tower": [s(F["filament_minimal_purge_on_wipe_tower"])],
    "filament_loading_speed": [s(F["filament_loading_speed"])],
    "filament_loading_speed_start": [s(F["filament_loading_speed_start"])],
    "filament_unloading_speed": [s(F["filament_unloading_speed"])],
    "filament_unloading_speed_start": [s(F["filament_unloading_speed_start"])],
    "filament_cooling_moves": [s(F["filament_cooling_moves"])],
    "filament_cooling_initial_speed": [s(F["filament_cooling_initial_speed"])],
    "filament_cooling_final_speed": [s(F["filament_cooling_final_speed"])],
    "filament_ramming_parameters": [F["filament_ramming_parameters"]],
    "filament_shrink": ["100%"],
    "filament_shrinkage_compensation_z": ["100%"],
    # No retraction overrides in the PrusaSlicer filament -> use printer values.
    "filament_retraction_length": ["nil"],
    "filament_retract_before_wipe": ["nil"],
    "filament_z_hop": ["nil"],
    "filament_z_hop_types": ["nil"],
    "filament_wipe": ["nil"],
})
# PrusaSlicer 3 has one bed temp per sheet; the CORE One satin/smooth sheets map to every Orca plate type.
for plate in ["hot_plate_temp", "textured_plate_temp", "cool_plate_temp", "eng_plate_temp",
              "textured_cool_plate_temp", "supertack_plate_temp"]:
    f[plate] = [bed]
    f[plate + "_initial_layer"] = [bed0]

# ---------------------------------------------------------------- process
p = load("process", "0.20mm STRUCTURAL @Prusa Prusa CORE One 0.4 nozzle")
fill_map = {"gyroid": "gyroid", "grid": "grid", "rectilinear": "zig-zag", "honeycomb": "honeycomb",
            "cubic": "cubic", "line": "line", "concentric": "concentric", "adaptivecubic": "adaptivecubic",
            "supportcubic": "supportcubic", "lightning": "lightning", "triangles": "triangles", "stars": "tri-hexagon"}
surf_map = {"monotonic": "monotonic", "monotoniclines": "monotonicline", "rectilinear": "zig-zag",
            "concentric": "concentric", "alignedrectilinear": "alignedrectilinear"}
p.update({
    "name": PROCESS,
    "print_settings_id": PROCESS,
    "inherits": "",
    "from": "User",
    "version": VERSION,
    "compatible_printers": [PRINTER],
    "compatible_printers_condition": "",
    "layer_height": s(S["layer_height"]),
    "initial_layer_print_height": s(S["first_layer_height"]),
    "line_width": s(S["extrusion_width"]),
    "outer_wall_line_width": s(S["external_perimeter_extrusion_width"]),
    "inner_wall_line_width": s(S["perimeter_extrusion_width"]),
    "sparse_infill_line_width": s(S["infill_extrusion_width"]),
    "internal_solid_infill_line_width": s(S["solid_infill_extrusion_width"]),
    "top_surface_line_width": s(S["top_infill_extrusion_width"]),
    "initial_layer_line_width": s(S["first_layer_extrusion_width"]),
    "support_line_width": s(S["support_material_extrusion_width"]),
    # Stock BALANCED values (the saved project had per-object tweaks: 6 walls, 40% gyroid, organic supports).
    "wall_loops": "2",
    "sparse_infill_density": "15%",
    "sparse_infill_pattern": "grid",
    "support_style": "snug",
    "top_shell_layers": s(S["top_solid_layers"]),
    "top_shell_thickness": s(S["top_solid_min_thickness"]),
    "bottom_shell_layers": s(S["bottom_solid_layers"]),
    "bottom_shell_thickness": s(S["bottom_solid_min_thickness"]),
    "top_surface_pattern": surf_map[S["top_fill_pattern"]],
    "bottom_surface_pattern": surf_map[S["bottom_fill_pattern"]],
    "infill_direction": s(S["fill_angle"]),
    "infill_wall_overlap": s(S["infill_overlap"]),
    "infill_anchor": s(S["infill_anchor"]),
    "infill_anchor_max": s(S["infill_anchor_max"]),
    "wall_generator": S["perimeter_generator"],
    "wall_sequence": "outer wall/inner wall" if S["external_perimeters_first"] else "inner wall/outer wall",
    "is_infill_first": s(S["infill_first"]),
    "detect_thin_wall": s(S["thin_walls"]),
    "ensure_vertical_shell_thickness": "ensure_all" if S["ensure_vertical_shell_thickness"] == "enabled" else "none",
    "seam_position": S["seam_position"],
    "seam_gap": s(S["seam_gap_distance"]),
    "staggered_inner_seams": s(S["staggered_inner_seams"]),
    "min_bead_width": s(S["min_bead_width"]),
    "min_feature_size": s(S["min_feature_size"]),
    "wall_transition_angle": s(S["wall_transition_angle"]),
    "wall_transition_filter_deviation": s(S["wall_transition_filter_deviation"]),
    "wall_transition_length": s(S["wall_transition_length"]),
    "wall_distribution_count": s(S["wall_distribution_count"]),
    "elefant_foot_compensation": s(S["elefant_foot_compensation"]),
    "xy_contour_compensation": s(S["xy_size_compensation"]),
    "slice_closing_radius": s(S["slice_closing_radius"]),
    "resolution": s(S["resolution"]),
    "enable_arc_fitting": "1" if S["arc_fitting"] != "disabled" else "0",
    "gcode_label_objects": "1" if S["gcode_label_objects"] != "disabled" else "0",
    "exclude_object": "1",
    "gcode_comments": s(S["gcode_comments"]),
    # speeds
    "outer_wall_speed": s(S["external_perimeter_speed"]),
    "inner_wall_speed": s(S["perimeter_speed"]),
    "small_perimeter_speed": s(S["small_perimeter_speed"]),
    "sparse_infill_speed": s(S["infill_speed"]),
    "internal_solid_infill_speed": s(S["solid_infill_speed"]),
    "top_surface_speed": s(S["top_solid_infill_speed"]),
    "gap_infill_speed": s(S["gap_fill_speed"]),
    "bridge_speed": s(S["bridge_speed"]),
    "internal_bridge_speed": "150%",
    "support_speed": s(S["support_material_speed"]),
    "support_interface_speed": f"{S['support_material_speed'] * S['support_material_interface_speed']['value'] / 100:g}",
    "initial_layer_speed": s(S["first_layer_speed"]),
    "initial_layer_infill_speed": s(S["first_layer_solid_infill_speed"]),
    "travel_speed": s(S["travel_speed"]),
    "travel_speed_z": s(S["travel_speed_z"]),
    "enable_overhang_speed": s(S["enable_dynamic_overhang_speeds"]),
    # PS overhang_speed_0..3 = 0%,25%,50%,75% overlap; Orca 1/4..4/4 = 10-25 %,25-50 %,50-75 %,75-100 % overhang
    "overhang_1_4_speed": s(S["overhang_speed_3"]),
    "overhang_2_4_speed": s(S["overhang_speed_2"]),
    "overhang_3_4_speed": s(S["overhang_speed_1"]),
    "overhang_4_4_speed": s(S["overhang_speed_0"]),
    # accelerations
    "default_acceleration": s(S["default_acceleration"]),
    "outer_wall_acceleration": s(S["external_perimeter_acceleration"]),
    "inner_wall_acceleration": s(S["perimeter_acceleration"]),
    "sparse_infill_acceleration": s(S["infill_acceleration"]),
    "internal_solid_infill_acceleration": s(S["solid_infill_acceleration"]),
    "top_surface_acceleration": s(S["top_solid_infill_acceleration"]),
    "bridge_acceleration": s(S["bridge_acceleration"]),
    "initial_layer_acceleration": s(S["first_layer_acceleration"]),
    "travel_acceleration": s(S["travel_acceleration"]),
    # bridges / ironing
    "bridge_flow": s(S["bridge_flow_ratio"]),
    "bridge_angle": s(S["bridge_angle"]),
    "thick_bridges": s(S["thick_bridges"]),
    "bridge_no_support": s(S["dont_support_bridges"]),
    "ironing_type": "no ironing" if not S["ironing"] else S["ironing_type"],
    "ironing_flow": s(S["ironing_flowrate"]),
    "ironing_spacing": s(S["ironing_spacing"]),
    "ironing_speed": s(S["ironing_speed"]),
    # supports (PS: enforcers only, build plate only)
    "enable_support": "1",
    "support_type": "normal(auto)",
    "enforce_support_layers": s(S["support_material_enforce_layers"]),
    "support_critical_regions_only": "0",
    "support_on_build_plate_only": s(S["support_material_buildplate_only"]),
    "support_threshold_angle": s(S["support_material_threshold"]),
    "support_top_z_distance": s(S["support_material_contact_distance"]),
    "support_bottom_z_distance": s(S["support_material_contact_distance"]),
    "support_interface_top_layers": s(S["support_material_interface_layers"]),
    "support_interface_bottom_layers": s(S["support_material_bottom_interface_layers"]),
    "support_interface_spacing": s(S["support_material_interface_spacing"]),
    "support_base_pattern_spacing": s(S["support_material_spacing"]),
    "support_angle": s(S["support_material_angle"]),
    "support_object_xy_distance": f"{0.45 * S['support_material_xy_spacing']['value'] / 100:g}",
    "tree_support_branch_angle_organic": s(S["support_tree_angle"]),
    "tree_support_angle_slow": s(S["support_tree_angle_slow"]),
    "tree_support_branch_diameter_organic": s(S["support_tree_branch_diameter"]),
    "tree_support_branch_diameter_angle": s(S["support_tree_branch_diameter_angle"]),
    "tree_support_tip_diameter": s(S["support_tree_tip_diameter"]),
    "tree_support_top_rate": s(S["support_tree_top_rate"]),
    "tree_support_branch_distance_organic": s(S["support_tree_branch_distance"]),
    "raft_layers": s(S["raft_layers"]),
    "raft_contact_distance": s(S["raft_contact_distance"]),
    "raft_expansion": s(S["raft_expansion"]),
    "raft_first_layer_density": s(S["raft_first_layer_density"]),
    "raft_first_layer_expansion": s(S["raft_first_layer_expansion"]),
    # skirt / brim
    "brim_type": "no_brim" if S["brim_type"] == "no_brim" else "outer_only",
    "brim_width": s(S["brim_width"]),
    "brim_object_gap": s(S["brim_separation"]),
    "skirt_loops": s(S["skirts"]),
    "skirt_distance": s(S["skirt_distance"]),
    "skirt_height": s(S["skirt_height"]),
    "min_skirt_length": s(S["min_skirt_length"]),
    # misc
    "print_sequence": "by object" if S["complete_objects"] else "by layer",
    "spiral_mode": s(S["spiral_vase"]),
    "fuzzy_skin": "disabled_fuzzy" if S["fuzzy_skin"] == "none" else S["fuzzy_skin"],
    "fuzzy_skin_point_distance": s(S["fuzzy_skin_point_dist"]),
    "fuzzy_skin_thickness": s(S["fuzzy_skin_thickness"]),
    "reduce_crossing_wall": s(S["avoid_crossing_perimeters"]),
    "max_travel_detour_distance": s(S["avoid_crossing_perimeters_max_detour"]),
    "standby_temperature_delta": s(S["standby_temperature_delta"]),
    "enable_prime_tower": s(S["wipe_tower"]),
    "prime_tower_width": s(S["wipe_tower_width"]),
    "prime_tower_brim_width": s(S["wipe_tower_brim_width"]),
    "wipe_tower_cone_angle": s(S["wipe_tower_cone_angle"]),
    "wipe_tower_bridging": s(S["wipe_tower_bridging"]),
    "wipe_tower_no_sparse_layers": s(S["wipe_tower_no_sparse_layers"]),
    "only_one_wall_first_layer": s(S["only_one_perimeter_first_layer"]),
    "interface_shells": s(S["interface_shells"]),
    # PrusaSlicer's {printing_filament_types} doesn't exist in Orca.
    # Also end in .bgcode: the Print button's USB helper writes binary G-code under the name picked
    # in Orca's upload dialog, and Orca only appends .gcode when the format has no extension.
    "filename_format": re.sub(r"\.gcode$", ".bgcode",
                              S["output_filename_format"].replace("{printing_filament_types}", "{filament_type[0]}")),
    "seam_slope_type": "none" if S["scarf_seam_placement"] == "nowhere" else "all",
    "seam_slope_min_length": s(S["scarf_seam_length"]),
})
# Others > Slicing Pipeline Plugin = "Export as bgcode" (orca_plugin/usb_bgcode_export): Export G-code
# to a .bgcode name saves binary G-code. Refs are "plugin_key;cloud_uuid;capability" (empty uuid for a
# local plugin, resolved by plugin_key = the .py file stem); the option itself holds bare capability names.
p["slicing_pipeline_plugin"] = ["Export as bgcode"]
p["plugins"] = ["usb_bgcode_export;;Export as bgcode"]

# ---------------------------------------------------------------- write
now = int(time.time())
for kind, d in [("machine", m), ("filament", f), ("process", p)]:
    os.makedirs(os.path.join(OUT, kind), exist_ok=True)
    base = os.path.join(OUT, kind, d["name"])
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(sorted(d.items())), fh, indent=4, ensure_ascii=False)
    with open(base + ".info", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"sync_info = \nuser_id = \nsetting_id = \nbase_id = \nupdated_time = {now}\n")
    print("wrote", base + ".json")
