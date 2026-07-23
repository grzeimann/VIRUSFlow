# Calibration cadence and Master Science

Calibration planning resolves exact raw row identities and metadata before it
creates or deduplicates a task. A numerical computation is identified by the
canonical kind, amplifier ZIP code, exact raw parents, algorithm/task version,
algorithm parameters, and configuration references. The grouping interval is
recorded as applicability; changing only a nominal boundary does not create a
second numerical computation.

## Default grouping

| Product | Scientific grouping |
|---|---|
| `master_bias` | UTC night; rolling 24-hour grouping is configurable. |
| `master_dark` | Calendar month; `weekly` is a supported override. |
| `master_twilight` | ISO week. |
| `master_ldls` | Isolated groups spanning at most three hours, with at least three exposures. |
| `master_hg` / `master_cd` | Separate isolated groups spanning at most three hours. |
| `master_arc` | One Hg and one Cd master, paired one-to-one by nearest temporal center within three hours, then summed. |
| `master_sci` | Eligible science exposures in a calendar month, explicit interval, or named observing block. |

Hg/Cd ties are resolved by center time and then stable group identity. An
unpaired lamp group remains unresolved; it is never silently merged with a
distant observation. LDLS reports retain each exposure's ambient temperature
and count, mean, median, minimum, maximum, spread, and missing-temperature
state. Registries scanned before these header fields were added should be
rescanned to populate `EXPTIME`, lamp, ambient-temperature, and observing-block
metadata; scanning is idempotent.

The lamp minimum counts, isolated-group span, and
`master_arc.cadence.maximum_pair_separation_hours` are configurable; their
defaults are one exposure, three hours, and three hours respectively.

`master_sci` requires raw `EXPTIME` (falling back explicitly to `PEXPTIME`) to
be strictly greater than 300 seconds. Exactly 300 seconds is excluded. The
provisional defaults require at least three eligible exposures and at least
1,800 total seconds. These are configurable acceptance criteria, not a claim
that the final signal-to-noise threshold is scientifically settled. A group
that fails any configured criterion is reported as unresolved and cannot
publish a normal `master_sci`. If `minimum_robust_illumination` is configured,
the plan marks that measurement pending; the task evaluates it after combining
and refuses publication when the measured value is below the threshold.

The canonical `master_sci` contains a robust `float32` detector aggregate and
`fiber_wavelength_mask_support`, a 2-D fractional robust-scatter plane. The
support plane is detector-coordinate evidence; downstream code projects it
through the trace and wavelength solutions when constructing or validating a
fiber-by-wavelength mask. It is not a scratch reduced-science image or a final
policy mask.

## Configuration and inspection

Purpose cadence is configured on the canonical node:

```yaml
version: 1
nodes:
  master_dark:
    cadence: {type: weekly, minimum_exposures: 1}
  master_sci:
    cadence:
      type: purpose
      policy: observing_block
      minimum_exposure_seconds: 300
      minimum_exposures: 3
      minimum_total_exposure_seconds: 1800
      intervals:
        - name: dark-2026-06
          start: 2026-06-08T00:00:00
          end: 2026-06-16T00:00:00
```

Run `virusflow run calibrations ... --plan-only` before execution. The emitted
`planning_report.yml` includes exact members, inclusion/exclusion reasons,
exposure-time and temperature statistics, sufficiency, computation identity,
applicability, lamp pairing/separation, deduplication, and downstream
requesters.
