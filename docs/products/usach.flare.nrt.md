# Model card: `usach.flare.nrt` (flare-nrt 1.x)

A summary of the producer's own model card,
[`docs/model-card.md` in `HelioUSACH/flare-nrt`](https://github.com/HelioUSACH/flare-nrt/blob/v1.0.0/docs/model-card.md)
(tag `v1.0.0`). The numbers below are quoted from it; that card is the
reference, and this summary follows it when it changes.

## Summary

**flare-nrt** detects M- and X-class solar flares in SDO/AIA synoptic
images, in near real time. For every 2-minute AIA slot it reports a
**probability**, an **alert** when the probability is ≥ 0.80, a **state**
(quiet / onset / active / decay; alerts less than 10 min apart form one
event), an **experimental location** on the disk while an event is open, and
a **Grad-CAM** map.

Maturity: **beta**. It is a research product, not an official warning, and
it is not ready for "operational": there are no live-period numbers yet,
and the threshold was set on the evaluation month itself.

**What it adds, honestly stated:** on detection alone it is beaten by a
simple rule on the disk-integrated 131 Å light curve (`aia131_rise`, below),
which detects more M/X flares. The model's demonstrated contribution is the
**location** of the flare on the disk (a median of 14″ from catalogue
positions) and the imaging confirmation, next to GOES X-rays.

## Inputs

Four AIA synoptic channels, **131, 171, 304 and 1600 Å**, 1024², level 1.5
quicklook, from JSOC (`synoptic/mostrecent/`, gaps filled from the dated
archive). Cadence 2 min; T_OBS to availability ≈ 7–10 min. A frame is not
used if it fails quality control or its newest T_OBS is older than 15 min.

## Method

An ensemble of 5 ResNet-18 networks; the probability is the mean of their
sigmoids, with no gating or smoothing. Each input is the 4 current channels
plus their difference against the mean of the frames 8, 10 and 12 min
earlier (at least 2 of the 3).

## Performance

Ground truth: the GOES flare list (SWPC). Evaluation period 2026-06-19 →
07-17, a replay of the synoptic archive (28.2 days, 54 M/X and 161 C
flares). The threshold was chosen on this month, so these numbers are
optimistic.

| | M/X detected | false alarms | first alert vs GOES start / peak (median) |
|---|---|---|---|
| **GOES XRS event start** (the reference that defines the labels) | 100 % by construction | 0 by construction | 0 min |
| **flare-nrt** (threshold 0.80) | **72 % (39/54)**; ≥ M2: 100 % (22/22); M1–M2: 53 % | **3** (≈ 1 per 9 days) | **+9 min / −1 min** |
| **`aia131_rise`** (no network, same frames; k = 4.9 %) | **98 % (53/54)** | 3 | +8 min / −2 min |
| `aia131_rise`, out of sample (k fitted on one half, scored on the other) | **98 % (53/54)** | 5 | |

- **Against GOES XRS (timing):** flare-nrt does not detect earlier. Its
  first alert comes a median 9 min after the GOES start and 1 min before
  the GOES peak.
- **Against `aia131_rise` (detection):** the network is beaten. The rule
  detects 14 more M/X flares with about the same time spent alerting, and
  that holds out of sample.
- **Location:** for 36 detected M/X flares with a catalogue position, the
  median distance to it is **14″**, and 89 % are within 120″.
- Independent 48 h check (2026-04-23 → 25, 10 M/X flares including X2.4 and
  X2.5): 10/10 detected, 0 false alarms.

## Known limitations

- **Baseline-age shortcut:** the output grows with the age of the baseline
  frames even without a flare (a training artefact), which forces the high
  threshold.
- **Grad-CAM in quiet Sun** can peak off the disk; the public map's colour
  scales with probability / threshold, so a quiet frame is a faint tint.
- **Domain gap:** trained on AIA level-1, run on synoptic quicklook data;
  the effect of the live inputs on skill is not measured yet.
- **Misses:** all 15 missed flares are M1.0–M1.4, most during very dense
  activity.
- **Quiet Sun under-tested:** the false-alarm rate over long quiet periods
  is not measured yet.
- **Location** is measured on one month only; limb flares and two
  simultaneous flares are its expected failure cases.

## On this portal

The portal polls the service's `/api/status` every minute (`modules/flare/`,
D-096, D-098) and stores each result once, at its AIA slot. The product page
shows the latest AIA images (four channels and their differences), the
last 6 h as a loop, the probability against the threshold with the GOES
X-ray flux for comparison, and the events of the last 30 days with the GOES
class the portal measured during each; choosing one shows its Grad-CAM over
131 Å at its peak (from the event's peak set kept by flare-nrt 1.2.0, so
an old event keeps its map) and its replay, and gives it a link
(`?evento=<id>`) (D-099 to D-112).

## Maintainers / links

- Producer repository: [`HelioUSACH/flare-nrt`](https://github.com/HelioUSACH/flare-nrt)
  (integration sheet `INTEGRATION.md`, full model card `docs/model-card.md`).
- Consumer side: `modules/flare/` in this repository;
  `docs/models/INTEGRATION.md` for the contract.
