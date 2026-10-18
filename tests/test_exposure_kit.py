import math
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import exposure_kit as ek  # noqa: E402


class ExposureValueTests(unittest.TestCase):
    def test_sunny_16_is_ev15_at_iso_100(self):
        # f/16 at 1/100 s, ISO 100: log2(256 * 100) = 14.64
        self.assertAlmostEqual(ek.exposure_value(16, 1 / 100, 100), math.log2(25600), places=6)

    def test_same_scene_gives_same_ev_at_any_iso(self):
        base = ek.exposure_value(16, 1 / 100, 100)
        at_400 = ek.exposure_value(16, 1 / 400, 400)
        self.assertAlmostEqual(base, at_400, places=9)

    def test_rejects_non_positive_inputs(self):
        with self.assertRaises(ValueError):
            ek.exposure_value(0, 1 / 100)


class EquivalentExposureTests(unittest.TestCase):
    def test_one_stop_more_light_from_iso_doubles_iso(self):
        options = ek.shift_exposure(8, 1 / 125, 100, 1)
        self.assertEqual(options["iso"][0], 200)
        self.assertAlmostEqual(options["iso"][1], 0.0, places=9)

    def test_aperture_shift_snaps_to_standard_stop_and_reports_error(self):
        # one stop more light from f/8 needs f/5.66, which snaps to f/5.6
        value, error = ek.shift_exposure(8, 1 / 125, 100, 1)["aperture"]
        self.assertEqual(value, 5.6)
        self.assertAlmostEqual(error, 2 * math.log2(8 / 5.6) - 1, places=9)
        self.assertAlmostEqual(error, 0.0291, places=3)

    def test_shutter_shift_snaps_to_standard_speed(self):
        value, _ = ek.shift_exposure(8, 1 / 125, 100, 1)["shutter"]
        self.assertAlmostEqual(value, 1 / 60)

    def test_zero_stops_is_rejected(self):
        with self.assertRaises(ValueError):
            ek.shift_exposure(8, 1 / 125, 100, 0)


class SunnyAndFilterTests(unittest.TestCase):
    def test_sunny16_exact_shutter(self):
        self.assertAlmostEqual(ek.sunny16_shutter(16, 100, 15), 1 / 128)

    def test_sunny16_scales_inversely_with_iso(self):
        self.assertAlmostEqual(ek.sunny16_shutter(16, 400, 15), 1 / 512)

    def test_nd_factor_eight_is_three_stops(self):
        self.assertAlmostEqual(ek.parse_filter("ND8"), 3.0)

    def test_nd_1000_is_about_ten_stops(self):
        self.assertAlmostEqual(ek.parse_filter("ND1000"), 9.966, places=3)

    def test_od_density_converts_to_stops(self):
        # OD 0.3 is one stop (factor 2)
        self.assertAlmostEqual(ek.parse_filter("OD0.3"), 0.9966, places=3)

    def test_filter_none_is_zero(self):
        self.assertEqual(ek.parse_filter("none"), 0.0)

    def test_bad_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            ek.parse_filter("ND0.5")

    def test_decimal_nd_label_is_rejected_as_density(self):
        with self.assertRaisesRegex(ValueError, "write OD3.0 instead"):
            ek.parse_filter("ND3.0")
        self.assertAlmostEqual(ek.parse_filter("OD3.0"), 3.0 * math.log2(10))


class ReciprocityTests(unittest.TestCase):
    def test_no_correction_at_or_below_one_second(self):
        self.assertEqual(ek.reciprocity_time(1.0), 1.0)
        self.assertEqual(ek.reciprocity_time(0.5), 0.5)

    def test_schwarzschild_power_law_above_one_second(self):
        self.assertAlmostEqual(ek.reciprocity_time(10, 1.31), 10 ** 1.31, places=9)


class DepthOfFieldTests(unittest.TestCase):
    def test_hyperfocal_50mm_f8_full_frame(self):
        # 50^2 / (8 * 0.03) + 50 = 10466.67 mm
        self.assertAlmostEqual(ek.hyperfocal_mm(50, 8, 0.03), 10466.667, places=2)

    def test_near_and_far_limits_at_3m(self):
        # closed form: near = s f^2 / (f^2 + N c (s - f)), far = s f^2 / (f^2 - N c (s - f))
        near, far = ek.depth_of_field_mm(50, 8, 3000, 0.03)
        self.assertAlmostEqual(near, 7_500_000 / 3208, delta=0.05)
        self.assertAlmostEqual(far, 7_500_000 / 1792, delta=0.05)

    def test_focus_at_hyperfocal_gives_near_limit_of_half(self):
        hyper = ek.hyperfocal_mm(50, 8, 0.03)
        near, far = ek.depth_of_field_mm(50, 8, hyper - 1, 0.03)
        self.assertAlmostEqual(near, hyper / 2, delta=1.0)

    def test_far_limit_is_infinite_past_hyperfocal(self):
        _, far = ek.depth_of_field_mm(50, 8, 20000, 0.03)
        self.assertTrue(math.isinf(far))

    def test_distance_inside_focal_length_is_rejected(self):
        with self.assertRaises(ValueError):
            ek.depth_of_field_mm(50, 8, 40, 0.03)


class FlashTests(unittest.TestCase):
    def test_guide_number_gives_aperture(self):
        self.assertAlmostEqual(ek.flash_aperture(36, 100, 10), 3.6)

    def test_doubling_iso_scales_guide_number_by_root_two(self):
        self.assertAlmostEqual(ek.flash_aperture(36, 400, 10), 7.2)

    def test_distance_from_aperture(self):
        self.assertAlmostEqual(ek.flash_distance(36, 100, 4), 9.0)


class StarLimitTests(unittest.TestCase):
    def test_500_rule_uses_crop_factor(self):
        self.assertAlmostEqual(ek.star_limits(20, 2.8, 1.0)["rule500_s"], 25.0)
        self.assertAlmostEqual(ek.star_limits(20, 2.8, 1.5)["rule500_s"], 500 / 30)

    def test_npf_rule(self):
        # (35 * 2.8 + 30 * 4.3) / 20 = 11.35 s
        self.assertAlmostEqual(ek.star_limits(20, 2.8, 1.0, 4.3)["npf_s"], 11.35)

    def test_npf_uses_actual_focal_length_not_crop_equivalent(self):
        # (35 * 2.8 + 30 * 3.9) / 20 = 10.75 s, even on a 1.5x body
        self.assertAlmostEqual(ek.star_limits(20, 2.8, 1.5, 3.9)["npf_s"], 10.75)


class ParserTests(unittest.TestCase):
    def test_glued_and_spaced_units_are_both_accepted(self):
        self.assertEqual(ek.parse_focal("50mm"), 50.0)
        self.assertEqual(ek.parse_focal("50"), 50.0)
        self.assertEqual(ek.parse_distance("250cm"), 2500.0)
        self.assertAlmostEqual(ek.parse_distance("10ft"), 3048.0)

    def test_aperture_and_shutter_forms(self):
        self.assertEqual(ek.parse_aperture("f/5.6"), 5.6)
        self.assertEqual(ek.parse_aperture("F8"), 8.0)
        self.assertAlmostEqual(ek.parse_shutter("1/125"), 1 / 125)
        self.assertEqual(ek.parse_shutter("2s"), 2.0)

    def test_bad_values_raise_with_hint(self):
        with self.assertRaisesRegex(ValueError, "use e.g. 50mm"):
            ek.parse_focal("fifty")
        with self.assertRaisesRegex(ValueError, "use e.g. 3m"):
            ek.parse_distance("3")

    def test_crop_accepts_x_suffix(self):
        self.assertEqual(ek.parse_crop("1.5x"), 1.5)


class PlanParserTests(unittest.TestCase):
    def test_valid_plan_yields_shots_with_line_numbers(self):
        text = "# comment\n\nportrait focal=85mm aperture=f/2 shutter=1/250 iso=400\n"
        shots, errors = ek.parse_plan(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0].line, 3)
        self.assertEqual(shots[0].focal_mm, 85.0)
        self.assertEqual(shots[0].iso, 400)

    def test_unknown_key_suggests_close_match(self):
        shots, errors = ek.parse_plan("a focal=50mm apature=f/8 shutter=1/60 iso=100\n")
        self.assertEqual(shots, [])
        self.assertEqual(errors[0][0], 1)
        self.assertIn("did you mean 'aperture'", errors[0][1])

    def test_missing_required_key_is_reported(self):
        _, errors = ek.parse_plan("a focal=50mm aperture=f/8 iso=100\n")
        self.assertIn("missing shutter", errors[0][1])

    def test_distance_must_exceed_focal_length(self):
        _, errors = ek.parse_plan("a focal=50mm aperture=f/8 shutter=1/60 iso=100 distance=40mm\n")
        self.assertIn("longer than the focal length", errors[0][1])

    def test_duplicate_shot_name_points_at_first_line(self):
        text = ("a focal=50mm aperture=f/8 shutter=1/60 iso=100\n"
                "a focal=35mm aperture=f/8 shutter=1/60 iso=100\n")
        shots, errors = ek.parse_plan(text)
        self.assertEqual(len(shots), 1)
        self.assertIn("first on line 1", errors[0][1])

    def test_bad_line_does_not_stop_later_lines(self):
        text = ("bad focal=oops aperture=f/8 shutter=1/60 iso=100\n"
                "good focal=50mm aperture=f/8 shutter=1/60 iso=100\n")
        shots, errors = ek.parse_plan(text)
        self.assertEqual([shot.name for shot in shots], ["good"])
        self.assertEqual(errors[0][0], 1)


class AnalysisTests(unittest.TestCase):
    def test_slow_handheld_shutter_is_warned(self):
        shot = ek.Shot("a", 1, 50, 8, 1 / 10, 100)
        warnings = ek.analyze_shot(shot)["warnings"]
        self.assertTrue(any("handheld limit" in message for message in warnings))

    def test_nonstandard_iso_and_aperture_are_warned(self):
        shot = ek.Shot("a", 1, 50, 7.5, 1 / 60, 333)
        warnings = " ".join(ek.analyze_shot(shot)["warnings"])
        self.assertIn("f/7.5 is not a standard stop (nearest f/7.1)", warnings)
        self.assertIn("ISO 333 is not a standard setting", warnings)

    def test_star_trail_warning_when_exposure_exceeds_npf(self):
        shot = ek.Shot("sky", 1, 20, 2.8, 30, 1600, crop=1.0, pitch_um=4.3)
        warnings = " ".join(ek.analyze_shot(shot)["warnings"])
        self.assertIn("stars will trail", warnings)


class CliTests(unittest.TestCase):
    def run_cli(self, argv):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ek.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_ev_command_prints_ev100(self):
        code, out, _ = self.run_cli(["ev", "f/16", "1/100"])
        self.assertEqual(code, 0)
        self.assertIn("EV100 14.64", out)

    def test_check_exit_code_reflects_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.txt"
            bad = Path(tmp) / "bad.txt"
            good.write_text("a focal=50mm aperture=f/8 shutter=1/60 iso=100\n", encoding="utf-8")
            bad.write_text("a focal=50mm aperture=f/8 shutter=1/60\n", encoding="utf-8")
            code_good, out_good, _ = self.run_cli(["check", str(good)])
            code_bad, out_bad, _ = self.run_cli(["check", str(bad)])
        self.assertEqual(code_good, 0)
        self.assertIn("1 shot(s), 0 error(s)", out_good)
        self.assertEqual(code_bad, 1)
        self.assertIn(":1: missing iso", out_bad)

    def test_bad_argument_returns_two_with_message(self):
        code, _, err = self.run_cli(["dof", "fifty", "f/8", "3m"])
        self.assertEqual(code, 2)
        self.assertIn("bad focal length", err)

    def test_example_plan_checks_clean(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "shots.txt"
        code, out, _ = self.run_cli(["check", str(example)])
        self.assertEqual(code, 0)
        self.assertIn("5 shot(s), 0 error(s)", out)


if __name__ == "__main__":
    unittest.main()
