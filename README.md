# Exposure Kit

**Offline photography exposure toolkit: EV, equivalent settings, filters, reciprocity, depth of field, flash and star-trail limits, plus a plain-text shot-plan checker with line-numbered errors.**

Exposure Kit answers the questions you ask on a shoot: what shutter goes with this aperture, how far in focus will I be, how long can the stars stay sharp, what does the ND filter do to my exposure. Every calculation is standard-library Python, runs offline, and has no accounts, servers or API keys.

## Features

- **Exposure value (EV100)** from aperture, shutter and ISO, with the EV at your actual ISO shown alongside.
- **Equivalent exposures**: shift a setting by any number of stops, change aperture, shutter or ISO on its own, and see the rounding error after snapping to standard values.
- **Sunny-16 starting points** for bright sun, hazy sun, cloudy-bright and overcast light.
- **ND and OD filters**: `ND8` (factor) or `OD0.6` (density) converted to stops, with the new shutter time. An `ND` label with a decimal point is rejected, since those are densities and must be written `OD`.
- **Reciprocity correction** for long film exposures, using the Schwarzschild power law with an adjustable exponent.
- **Hyperfocal distance** and **near/far sharp limits** for any focus distance, with crop-factor support.
- **Flash math** from a guide number: aperture for a distance, or the reach of the flash at an aperture, with ISO scaling.
- **Star-trail limits** from the 500 rule and the NPF rule (which needs your sensor's pixel pitch).
- **Shot-plan checker** (`check`) for a plain-text file of planned shots, with line-numbered errors, close-match hints for typos in key names, and per-shot warnings.
- **Warnings** for handheld shutter limits, reciprocity over one second, non-standard apertures and ISO values, and exposures that will trail stars.
- **Scriptable exit codes**: `0` clean, `1` the plan has errors, `2` bad command-line input.

## Quick start

Requires Python 3.10 or newer. No install step.

```bash
python3 exposure_kit.py ev f/16 1/100 --iso 100
```

```text
EV100 14.64  (f/16 at 1/100, ISO 100)
EV at ISO 100: 14.64
```

Work out the focus limits for a 50mm lens at f/8, focused on a subject 3 metres away:

```bash
python3 exposure_kit.py dof 50mm f/8 3m
```

```text
50mm f/8 focused at 3.00m
  hyperfocal 10.47m
  sharp from 2.34m to 4.19m (depth 1.85m)
```

Check a whole shoot plan. The file `examples/shots.txt` contains five planned shots:

```bash
python3 exposure_kit.py check examples/shots.txt
```

A plan line looks like this (`#` starts a comment):

```text
portrait-tele  focal=85mm aperture=f/2 shutter=1/250 iso=400 distance=2.5m
```

A typo in a plan produces a line-numbered error:

```text
plan.txt:4: unknown key 'apature' (did you mean 'aperture'?)
```

## Commands

| Command | What it does | Example |
| --- | --- | --- |
| `ev` | EV100 for aperture, shutter and ISO | `ev f/8 1/125 --iso 200` |
| `shift` | Equivalent exposure for a change of stops | `shift f/8 1/125 100 1` |
| `sunny16` | Starting shutter for a light level | `sunny16 400 --light overcast` |
| `nd` | Shutter after an ND or OD filter | `nd ND8 1/4` |
| `reciprocity` | Film correction for long exposures | `reciprocity 10s --exponent 1.31` |
| `dof` | Near and far sharp limits | `dof 35mm f/5.6 4m --crop 1.5` |
| `hyperfocal` | Hyperfocal distance | `hyperfocal 24mm f/11` |
| `flash` | Flash aperture or reach from a guide number | `flash 36 100 --distance 4m` |
| `trails` | 500 rule and NPF limit | `trails 20mm f/2.8 --crop 1.5 --pitch 3.9` |
| `check` | Lint and analyse a shot-plan file | `check examples/shots.txt` |

Lengths accept `mm`, `cm`, `m` or `ft`. Apertures accept `f/8`, `f8` or `8`. Shutter speeds accept `1/125`, `2`, `2s` or `1/125s`.

## Shot-plan format

One shot per line. The first word is the shot name. The rest are `key=value` pairs, separated by spaces.

| Key | Required | Example |
| --- | --- | --- |
| `focal` | yes | `focal=85mm` |
| `aperture` | yes | `aperture=f/2.8` |
| `shutter` | yes | `shutter=1/250` |
| `iso` | yes | `iso=400` |
| `distance` | no | `distance=2.5m` (enables depth of field) |
| `filter` | no | `filter=ND8` or `filter=OD0.6` |
| `crop` | no | `crop=1.5x` (APS-C style sensors) |
| `pitch` | no | `pitch=3.9um` (enables the NPF rule) |

Each key may appear once per line. Shot names must be unique.

## How to run

The CLI is a single file, so you can run it from the project directory or call it with a full path:

```bash
python3 exposure_kit.py --help
python3 exposure_kit.py check examples/shots.txt; echo "exit $?"
```

## How to test

The test suite uses `unittest`, so it runs with the standard library alone:

```bash
python3 -m unittest discover -s tests -v
```

Pytest also collects these tests unchanged:

```bash
python3 -m pytest tests -v
```

The tests check the core formulas against published reference values (hyperfocal, depth of field, NPF, the sunny-16 relationship), the plan parser's line-numbered errors, and the CLI exit codes.

## Notes on the numbers

- Depth of field uses a circle of confusion of 0.03 mm for full frame, divided by the crop factor. Change this for other sensors or print sizes via the Python functions.
- The reciprocity exponent of 1.31 is a generic Schwarzschild value. Use the figure from your film's datasheet with `--exponent`.
- Light levels for `sunny16` are approximate starting points, not light meter readings.

## Project layout

```text
exposure_kit.py          the calculator, plan parser and CLI
examples/shots.txt       a five-shot sample plan
tests/test_exposure_kit.py
README.md
```
