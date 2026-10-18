#!/usr/bin/env python3
"""Exposure Kit: exposure value, equivalent settings, filters, reciprocity,
depth of field, flash and star-trail limits, plus a plain-text shot-plan checker.

Standard library only. Everything runs offline.
"""

import argparse
import difflib
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

COC_FULL_FRAME_MM = 0.03
DEFAULT_RECIPROCITY_EXPONENT = 1.31
LIGHT_EV100 = {"bright-sun": 15, "hazy-sun": 14, "cloudy-bright": 13, "overcast": 12}

# Third-stop aperture series (ISO 517 style rounded values).
APERTURE_STOPS = [1, 1.1, 1.2, 1.4, 1.6, 1.8, 2, 2.2, 2.5, 2.8, 3.2, 3.5, 4, 4.5, 5,
                  5.6, 6.3, 7.1, 8, 9, 10, 11, 13, 14, 16, 18, 20, 22, 25, 29, 32,
                  36, 40, 45, 51, 57, 64]
SHUTTER_SECONDS = sorted(
    [30, 25, 20, 15, 13, 10, 8, 6, 5, 4, 3.2, 2.5, 2, 1.6, 1.3, 1, 0.8, 0.6, 0.5, 0.4, 0.3]
    + [1 / d for d in (4, 5, 6, 8, 10, 13, 15, 20, 25, 30, 40, 50, 60, 80, 100, 125, 160,
                       200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600, 2000, 2500,
                       3200, 4000, 5000, 6400, 8000)]
)
ISO_STOPS = [25, 32, 40, 50, 64, 80, 100, 125, 160, 200, 250, 320, 400, 500, 640, 800,
             1000, 1250, 1600, 2000, 2500, 3200, 4000, 5000, 6400, 8000, 10000, 12800,
             16000, 20000, 25600, 51200, 102400]

_NUM = r"\d+(?:\.\d+)?"
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "ft": 304.8}


# ---------------------------------------------------------------- value parsers

def parse_focal(text):
    """'50mm' or '50' -> millimetres."""
    match = re.fullmatch(rf"({_NUM})(?:mm)?", text.strip(), re.IGNORECASE)
    if not match or float(match.group(1)) <= 0:
        raise ValueError(f"bad focal length '{text}' (use e.g. 50mm)")
    return float(match.group(1))


def parse_aperture(text):
    """'f/8', 'f8' or '8' -> f-number."""
    body = text.strip().lower()
    if body.startswith("f/"):
        body = body[2:]
    elif body.startswith("f"):
        body = body[1:]
    if not re.fullmatch(_NUM, body) or float(body) <= 0:
        raise ValueError(f"bad aperture '{text}' (use e.g. f/8)")
    return float(body)


def parse_shutter(text):
    """'1/125', '2', '2s' or '1/125s' -> seconds."""
    body = text.strip().lower()
    if body.endswith("s") and len(body) > 1:
        body = body[:-1]
    match = re.fullmatch(rf"({_NUM})(?:/({_NUM}))?", body)
    if not match:
        raise ValueError(f"bad shutter speed '{text}' (use e.g. 1/125 or 2s)")
    numerator = float(match.group(1))
    denominator = float(match.group(2)) if match.group(2) else 1.0
    if numerator <= 0 or denominator <= 0:
        raise ValueError(f"shutter speed must be positive, got '{text}'")
    return numerator / denominator


def parse_iso(text):
    """'200' or 'ISO200' -> integer ISO."""
    body = text.strip().lower()
    if body.startswith("iso"):
        body = body[3:]
    if not re.fullmatch(r"\d+", body) or int(body) <= 0:
        raise ValueError(f"bad ISO '{text}' (use e.g. 200)")
    return int(body)


def parse_distance(text):
    """'3m', '250cm', '10ft' -> millimetres."""
    match = re.fullmatch(rf"({_NUM})(mm|cm|m|ft)", text.strip(), re.IGNORECASE)
    if not match or float(match.group(1)) <= 0:
        raise ValueError(f"bad distance '{text}' (use e.g. 3m, 250cm or 10ft)")
    return float(match.group(1)) * _UNIT_MM[match.group(2).lower()]


def parse_filter(text):
    """'none', 'ND8' (factor) or 'OD0.6' (density) -> stops of light removed."""
    body = text.strip()
    if body.lower() in ("", "none"):
        return 0.0
    match = re.fullmatch(rf"(nd|od)({_NUM})", body, re.IGNORECASE)
    if not match:
        raise ValueError(f"bad filter '{text}' (use e.g. ND8, OD0.6 or none)")
    kind, value = match.group(1).lower(), float(match.group(2))
    if kind == "nd":
        if "." in match.group(2):
            raise ValueError(f"'{text}' is a density label; write OD{match.group(2)} instead")
        if value < 1:
            raise ValueError(f"ND factor must be at least 1, got '{text}'")
        return math.log2(value)
    if value <= 0:
        raise ValueError(f"OD density must be positive, got '{text}'")
    return value * math.log2(10)


def parse_crop(text):
    """'1.5' or '1.5x' -> crop factor (1 = full frame)."""
    match = re.fullmatch(rf"({_NUM})x?", text.strip(), re.IGNORECASE)
    if not match or float(match.group(1)) <= 0:
        raise ValueError(f"bad crop factor '{text}' (use e.g. 1.5 or 1.5x)")
    return float(match.group(1))


def parse_pitch(text):
    """'4.3' or '4.3um' -> pixel pitch in micrometres."""
    match = re.fullmatch(rf"({_NUM})(?:um)?", text.strip(), re.IGNORECASE)
    if not match or float(match.group(1)) <= 0:
        raise ValueError(f"bad pixel pitch '{text}' (use e.g. 4.3um)")
    return float(match.group(1))


# ---------------------------------------------------------------- core math

def snap(value, table):
    """Nearest entry of table to value, measured as a ratio (log distance)."""
    if value <= 0:
        raise ValueError("can only snap positive values")
    return min(table, key=lambda entry: abs(math.log(entry / value)))


def fmt_shutter(seconds):
    if seconds >= 0.4:
        return f"{seconds:g}s"
    return f"1/{round(1 / seconds)}"


def fmt_metres(mm):
    return "infinite" if math.isinf(mm) else f"{mm / 1000:.2f}m"


def exposure_value(aperture, shutter_s, iso=100):
    """EV normalised to ISO 100: EV100 = log2(N^2 / t) - log2(ISO / 100)."""
    if aperture <= 0 or shutter_s <= 0 or iso <= 0:
        raise ValueError("aperture, shutter and ISO must be positive")
    return math.log2(aperture ** 2 / shutter_s) - math.log2(iso / 100)


def shift_exposure(aperture, shutter_s, iso, stops):
    """Move the exposure by `stops` (positive = more light), one axis at a time.

    Returns {axis: (new_value, error_stops)} where error_stops is the gap between
    the light actually gained after snapping to standard values and the request.
    """
    if stops == 0:
        raise ValueError("stops must not be zero")
    new_n = snap(aperture / 2 ** (stops / 2), APERTURE_STOPS)
    new_t = snap(shutter_s * 2 ** stops, SHUTTER_SECONDS)
    new_iso = snap(iso * 2 ** stops, ISO_STOPS)
    return {
        "aperture": (new_n, 2 * math.log2(aperture / new_n) - stops),
        "shutter": (new_t, math.log2(new_t / shutter_s) - stops),
        "iso": (new_iso, math.log2(new_iso / iso) - stops),
    }


def sunny16_shutter(aperture, iso=100, ev100=15):
    """Shutter time for a given aperture and ISO under light of the given EV100."""
    if aperture <= 0 or iso <= 0:
        raise ValueError("aperture and ISO must be positive")
    return aperture ** 2 * 100 / (2 ** ev100 * iso)


def reciprocity_time(shutter_s, exponent=DEFAULT_RECIPROCITY_EXPONENT):
    """Schwarzschild correction for film: t_corrected = t ** p above one second."""
    if shutter_s <= 1:
        return shutter_s
    return shutter_s ** exponent


def hyperfocal_mm(focal_mm, aperture, coc_mm=COC_FULL_FRAME_MM):
    """Focus distance that makes everything from half of it to infinity acceptably sharp."""
    return focal_mm ** 2 / (aperture * coc_mm) + focal_mm


def depth_of_field_mm(focal_mm, aperture, distance_mm, coc_mm=COC_FULL_FRAME_MM):
    """Return (near, far) limits in mm for focus at distance_mm. far is inf past hyperfocal."""
    if distance_mm <= focal_mm:
        raise ValueError("subject distance must be longer than the focal length")
    hyper = hyperfocal_mm(focal_mm, aperture, coc_mm)
    near = (hyper - focal_mm) * distance_mm / (hyper + distance_mm - 2 * focal_mm)
    if distance_mm >= hyper:
        return near, math.inf
    far = (hyper - focal_mm) * distance_mm / (hyper - distance_mm)
    return near, far


def flash_aperture(guide_number, iso, distance_m):
    """Aperture for a flash GN (given at ISO 100, metres) and subject distance.

    Guide numbers scale with the square root of ISO.
    """
    if distance_m <= 0:
        raise ValueError("flash distance must be positive")
    return guide_number * math.sqrt(iso / 100) / distance_m


def flash_distance(guide_number, iso, aperture):
    """Maximum flash distance in metres for an aperture."""
    if aperture <= 0:
        raise ValueError("aperture must be positive")
    return guide_number * math.sqrt(iso / 100) / aperture


def star_limits(focal_mm, aperture, crop=1.0, pitch_um=None):
    """Longest sensible exposure for points of light: 500 rule and NPF rule (seconds).

    The 500 rule uses the 35mm-equivalent focal length. NPF uses the actual focal
    length, because pixel pitch is a physical size and star motion scales with it.
    """
    equiv = focal_mm * crop
    npf = None
    if pitch_um is not None:
        npf = (35 * aperture + 30 * pitch_um) / focal_mm
    return {"rule500_s": 500 / equiv, "npf_s": npf}


# ---------------------------------------------------------------- shot plans

REQUIRED_KEYS = ("focal", "aperture", "shutter", "iso")
KEY_PARSERS = {
    "focal": parse_focal,
    "aperture": parse_aperture,
    "shutter": parse_shutter,
    "iso": parse_iso,
    "distance": parse_distance,
    "filter": parse_filter,
    "crop": parse_crop,
    "pitch": parse_pitch,
}


@dataclass
class Shot:
    name: str
    line: int
    focal_mm: float
    aperture: float
    shutter_s: float
    iso: int
    distance_mm: float | None = None
    filter_stops: float = 0.0
    crop: float = 1.0
    pitch_um: float | None = None


def parse_plan(text):
    """Parse a shot-plan file. Returns (shots, errors) where errors are (line, message).

    Each line is: name key=value key=value ...   (# starts a comment)
    """
    shots, errors = [], []
    first_seen = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        tokens = body.split()
        name = tokens[0]
        if "=" in name:
            errors.append((line_no, f"expected a shot name before '{name}'"))
            continue
        values, line_errors = {}, []
        for token in tokens[1:]:
            if "=" not in token:
                line_errors.append(f"expected key=value, got '{token}'")
                continue
            key, _, value_text = token.partition("=")
            key = key.lower()
            if key not in KEY_PARSERS:
                hint = difflib.get_close_matches(key, KEY_PARSERS, n=1)
                suffix = f" (did you mean '{hint[0]}'?)" if hint else ""
                line_errors.append(f"unknown key '{key}'{suffix}")
                continue
            if key in values:
                line_errors.append(f"duplicate key '{key}'")
                continue
            try:
                values[key] = KEY_PARSERS[key](value_text)
            except ValueError as exc:
                line_errors.append(str(exc))
        missing = [key for key in REQUIRED_KEYS if key not in values]
        if missing:
            line_errors.append("missing " + ", ".join(missing))
        if "distance" in values and "focal" in values and values["distance"] <= values["focal"]:
            line_errors.append("distance must be longer than the focal length")
        if name.lower() in first_seen:
            line_errors.append(f"duplicate shot name '{name}' (first on line {first_seen[name.lower()]})")
        if line_errors:
            errors.extend((line_no, message) for message in line_errors)
            continue
        first_seen[name.lower()] = line_no
        shots.append(Shot(
            name=name,
            line=line_no,
            focal_mm=values["focal"],
            aperture=values["aperture"],
            shutter_s=values["shutter"],
            iso=values["iso"],
            distance_mm=values.get("distance"),
            filter_stops=values.get("filter", 0.0),
            crop=values.get("crop", 1.0),
            pitch_um=values.get("pitch"),
        ))
    return shots, errors


def analyze_shot(shot):
    """Compute the numbers and warnings for one shot."""
    equiv = shot.focal_mm * shot.crop
    coc = COC_FULL_FRAME_MM / shot.crop
    effective = shot.shutter_s * 2 ** shot.filter_stops
    handheld_limit = 1 / equiv
    warnings = []

    if effective > handheld_limit:
        warnings.append(f"{fmt_shutter(effective)} is slower than the handheld limit "
                        f"{fmt_shutter(handheld_limit)}; use a tripod or a faster shutter")
    if effective > 1:
        corrected = reciprocity_time(effective)
        warnings.append(f"over 1s: on film, reciprocity needs {fmt_shutter(corrected)} "
                        f"(+{math.log2(corrected / effective):.2f} stops)")

    nearest_n = snap(shot.aperture, APERTURE_STOPS)
    if abs(nearest_n - shot.aperture) / shot.aperture > 0.02:
        warnings.append(f"f/{shot.aperture:g} is not a standard stop (nearest f/{nearest_n:g})")
    if shot.iso not in ISO_STOPS:
        nearest_iso = snap(shot.iso, ISO_STOPS)
        warnings.append(f"ISO {shot.iso} is not a standard setting (nearest {nearest_iso})")

    report = {
        "ev100": exposure_value(shot.aperture, shot.shutter_s, shot.iso),
        "effective_shutter_s": effective,
        "equiv_focal_mm": equiv,
        "handheld_limit_s": handheld_limit,
        "hyperfocal_mm": hyperfocal_mm(shot.focal_mm, shot.aperture, coc),
        "dof": None,
        "star_limits": None,
        "warnings": warnings,
    }
    if shot.distance_mm is not None:
        near, far = depth_of_field_mm(shot.focal_mm, shot.aperture, shot.distance_mm, coc)
        report["dof"] = {"near_mm": near, "far_mm": far}
    limits = star_limits(shot.focal_mm, shot.aperture, shot.crop, shot.pitch_um)
    report["star_limits"] = limits
    if limits["npf_s"] is not None and effective > limits["npf_s"]:
        warnings.append(f"exposure exceeds the NPF limit {fmt_shutter(limits['npf_s'])}; "
                        "stars will trail")
    return report


def format_shot(shot, report):
    lines = [
        f"line {shot.line:<4} {shot.name}: {shot.focal_mm:g}mm f/{shot.aperture:g} "
        f"{fmt_shutter(shot.shutter_s)} ISO {shot.iso}",
        f"  EV100 {report['ev100']:.2f} | effective {fmt_shutter(report['effective_shutter_s'])} "
        f"| equivalent {report['equiv_focal_mm']:g}mm | hyperfocal {fmt_metres(report['hyperfocal_mm'])}",
    ]
    if report["dof"]:
        dof = report["dof"]
        total = dof["far_mm"] - dof["near_mm"]
        lines.append(f"  focus at {fmt_metres(shot.distance_mm)}: sharp from "
                     f"{fmt_metres(dof['near_mm'])} to {fmt_metres(dof['far_mm'])} "
                     f"(depth {fmt_metres(total)})")
    if report["star_limits"]:
        limits = report["star_limits"]
        npf = fmt_shutter(limits["npf_s"]) if limits["npf_s"] is not None else "needs pitch="
        lines.append(f"  star limits: 500 rule {fmt_shutter(limits['rule500_s'])}, NPF {npf}")
    lines.extend(f"  warning: {message}" for message in report["warnings"])
    return lines


# ---------------------------------------------------------------- CLI

def cmd_ev(args):
    aperture = parse_aperture(args.aperture)
    shutter = parse_shutter(args.shutter)
    iso = parse_iso(args.iso)
    ev100 = exposure_value(aperture, shutter, iso)
    print(f"EV100 {ev100:.2f}  (f/{aperture:g} at {fmt_shutter(shutter)}, ISO {iso})")
    print(f"EV at ISO {iso}: {ev100 + math.log2(iso / 100):.2f}")
    return 0


def cmd_shift(args):
    aperture = parse_aperture(args.aperture)
    shutter = parse_shutter(args.shutter)
    iso = parse_iso(args.iso)
    options = shift_exposure(aperture, shutter, iso, args.stops)
    print(f"Shifting f/{aperture:g} {fmt_shutter(shutter)} ISO {iso} by {args.stops:+g} stops:")
    labels = {"aperture": f"f/{aperture:g}", "shutter": fmt_shutter(shutter), "iso": str(iso)}
    for axis, (value, error) in options.items():
        shown = f"f/{value:g}" if axis == "aperture" else (
            fmt_shutter(value) if axis == "shutter" else str(value))
        print(f"  {axis:<9} {labels[axis]} -> {shown:<8} (rounding error {error:+.2f} stops)")
    return 0


def cmd_sunny16(args):
    iso = parse_iso(args.iso)
    aperture = parse_aperture(args.aperture)
    if args.light not in LIGHT_EV100:
        raise ValueError(f"unknown light '{args.light}' (choose from {', '.join(LIGHT_EV100)})")
    ev100 = LIGHT_EV100[args.light]
    exact = sunny16_shutter(aperture, iso, ev100)
    print(f"{args.light} (EV100 {ev100}), f/{aperture:g}, ISO {iso}: "
          f"{fmt_shutter(exact)} exact, {fmt_shutter(snap(exact, SHUTTER_SECONDS))} standard")
    return 0


def cmd_nd(args):
    stops = parse_filter(args.filter)
    shutter = parse_shutter(args.shutter)
    effective = shutter * 2 ** stops
    print(f"{args.filter} removes {stops:.2f} stops: {fmt_shutter(shutter)} -> "
          f"{fmt_shutter(effective)} ({fmt_shutter(snap(effective, SHUTTER_SECONDS))} standard)")
    return 0


def cmd_reciprocity(args):
    shutter = parse_shutter(args.shutter)
    if args.exponent <= 0:
        raise ValueError("reciprocity exponent must be positive")
    corrected = reciprocity_time(shutter, args.exponent)
    if shutter <= 1:
        print(f"{fmt_shutter(shutter)}: no reciprocity correction needed under 1s")
    else:
        print(f"{fmt_shutter(shutter)} -> {corrected:.1f}s with p={args.exponent:g} "
              f"(+{math.log2(corrected / shutter):.2f} stops)")
    return 0


def cmd_dof(args):
    focal = parse_focal(args.focal)
    aperture = parse_aperture(args.aperture)
    distance = parse_distance(args.distance)
    crop = parse_crop(args.crop)
    near, far = depth_of_field_mm(focal, aperture, distance, COC_FULL_FRAME_MM / crop)
    hyper = hyperfocal_mm(focal, aperture, COC_FULL_FRAME_MM / crop)
    print(f"{focal:g}mm f/{aperture:g} focused at {fmt_metres(distance)}")
    print(f"  hyperfocal {fmt_metres(hyper)}")
    print(f"  sharp from {fmt_metres(near)} to {fmt_metres(far)} "
          f"(depth {fmt_metres(far - near)})")
    return 0


def cmd_hyperfocal(args):
    focal = parse_focal(args.focal)
    aperture = parse_aperture(args.aperture)
    crop = parse_crop(args.crop)
    hyper = hyperfocal_mm(focal, aperture, COC_FULL_FRAME_MM / crop)
    print(f"{focal:g}mm f/{aperture:g}: focus at {fmt_metres(hyper)}, "
          f"sharp from {fmt_metres(hyper / 2)} to infinity")
    return 0


def cmd_flash(args):
    guide = float(args.guide)
    iso = parse_iso(args.iso)
    if guide <= 0:
        raise ValueError("guide number must be positive")
    if args.distance is not None:
        metres = parse_distance(args.distance) / 1000
        aperture = flash_aperture(guide, iso, metres)
        print(f"GN {guide:g} at ISO {iso}, subject at {metres:.2f}m: f/{aperture:.1f}")
    else:
        aperture = parse_aperture(args.aperture)
        metres = flash_distance(guide, iso, aperture)
        print(f"GN {guide:g} at ISO {iso}, f/{aperture:g}: flash reaches {metres:.2f}m")
    return 0


def cmd_trails(args):
    focal = parse_focal(args.focal)
    aperture = parse_aperture(args.aperture)
    crop = parse_crop(args.crop)
    pitch = parse_pitch(args.pitch) if args.pitch else None
    limits = star_limits(focal, aperture, crop, pitch)
    print(f"{focal:g}mm f/{aperture:g}: 500 rule {fmt_shutter(limits['rule500_s'])}")
    if limits["npf_s"] is None:
        print("  NPF rule needs --pitch (pixel pitch in um)")
    else:
        print(f"  NPF rule {fmt_shutter(limits['npf_s'])} (pitch {pitch:g}um)")
    return 0


def cmd_check(args):
    path = Path(args.plan)
    shots, errors = parse_plan(path.read_text(encoding="utf-8"))
    for line_no, message in errors:
        print(f"{path}:{line_no}: {message}")
    warning_count = 0
    for shot in shots:
        report = analyze_shot(shot)
        warning_count += len(report["warnings"])
        print("\n".join(format_shot(shot, report)))
    print(f"{len(shots)} shot(s), {len(errors)} error(s), {warning_count} warning(s)")
    return 1 if errors else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="exposure-kit",
        description="Exposure, depth-of-field, flash and star-trail calculator with a shot-plan checker.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ev", help="exposure value for aperture, shutter and ISO")
    p.add_argument("aperture")
    p.add_argument("shutter")
    p.add_argument("--iso", default="100")
    p.set_defaults(func=cmd_ev)

    p = sub.add_parser("shift", help="equivalent exposures for a change of stops")
    p.add_argument("aperture")
    p.add_argument("shutter")
    p.add_argument("iso")
    p.add_argument("stops", type=float, help="positive = more light, negative = less")
    p.set_defaults(func=cmd_shift)

    p = sub.add_parser("sunny16", help="starting exposure from the sunny-16 family of rules")
    p.add_argument("iso")
    p.add_argument("--aperture", default="16")
    p.add_argument("--light", default="bright-sun", choices=sorted(LIGHT_EV100))
    p.set_defaults(func=cmd_sunny16)

    p = sub.add_parser("nd", help="shutter time after an ND or OD filter")
    p.add_argument("filter", help="e.g. ND8, OD0.6 or none")
    p.add_argument("shutter")
    p.set_defaults(func=cmd_nd)

    p = sub.add_parser("reciprocity", help="film reciprocity correction for long exposures")
    p.add_argument("shutter")
    p.add_argument("--exponent", type=float, default=DEFAULT_RECIPROCITY_EXPONENT)
    p.set_defaults(func=cmd_reciprocity)

    p = sub.add_parser("dof", help="near and far sharp limits")
    p.add_argument("focal")
    p.add_argument("aperture")
    p.add_argument("distance")
    p.add_argument("--crop", default="1")
    p.set_defaults(func=cmd_dof)

    p = sub.add_parser("hyperfocal", help="hyperfocal distance")
    p.add_argument("focal")
    p.add_argument("aperture")
    p.add_argument("--crop", default="1")
    p.set_defaults(func=cmd_hyperfocal)

    p = sub.add_parser("flash", help="flash aperture or reach from a guide number")
    p.add_argument("guide", help="guide number at ISO 100 in metres")
    p.add_argument("iso")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--distance", help="subject distance, e.g. 3m")
    group.add_argument("--aperture", help="lens aperture, e.g. f/8")
    p.set_defaults(func=cmd_flash)

    p = sub.add_parser("trails", help="longest exposure before stars trail")
    p.add_argument("focal")
    p.add_argument("aperture")
    p.add_argument("--crop", default="1")
    p.add_argument("--pitch", help="pixel pitch in um, enables the NPF rule")
    p.set_defaults(func=cmd_trails)

    p = sub.add_parser("check", help="lint and analyse a shot-plan file")
    p.add_argument("plan", help="path to a shot-plan text file")
    p.set_defaults(func=cmd_check)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"exposure-kit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
