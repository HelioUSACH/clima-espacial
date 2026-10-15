# Model card: `<product.id>`

_Copy this file to `docs/products/<product.id>.md` and fill it in — the
manifest's `model_card` field should point at the result. Keep it current:
a model card is a living document, not a one-time thesis write-up._

## Summary

One paragraph: what it predicts, for whom, and its current maturity
(`experimental` / `beta` / `operational` — must match the manifest).

## Inputs

What it reads, from where, at what cadence/latency.

## Method

Architecture in a sentence or two (e.g. "ensemble of N ResNet-18 nets on
8-channel SDO/AIA input"); link to the paper/thesis/repo for detail — don't
duplicate it here.

## Training data

What it was trained on, the time range, and anything about that data a
reader needs to judge generalisation (e.g. solar-cycle phase, instrument
changes).

## Performance

Required before `maturity` can move past `experimental`: skill **against a
named baseline** (persistence and/or climatology — never performance in a
vacuum), measured on live/held-out data, with the evaluation window stated.
Update this section as more live history accumulates — don't let a launch
number go stale for a year.

## Known limitations

Be specific and current — e.g. "a low-probability 2-frame alternation is
under diagnosis (see the producer repo's issue tracker)". A model card that
only lists strengths isn't one.

## Maintainers / links

Producer repository, maintainer(s), and anything else worth two seconds to
find quickly (ops contact, where weights live).
