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

The canonical `master_sci` contains only the robust `float32`
detector-coordinate aggregate. Two separately persisted downstream products
make its spectral use explicit:

```text
master_sci + trace_map
  -> extracted_master_sci_spectrum

extracted_master_sci_spectrum + wavelength_map
  -> fiber_wavelength_spectral_mask
```

The extraction uses the configured fractional five-pixel aperture and retains
valid-pixel fraction, effective aperture width, and extraction validity. The
mask artifact retains the mask, wavelength-space spectral model, applied fiber
normalization, and per-fiber wavelength-solution usability. If a compatible
twilight-derived `within_amp_fiber_normalization` is supplied explicitly, it is
recorded as an additional parent. The default graph instead removes broad fiber
throughput with deterministic, recorded coarse-bin self-normalization. The former
`fiber_wavelength_mask_support` detector-stack scatter plane is no longer
created or interpreted as spectral-mask evidence.

Existing `master_sci` revisions are immutable and remain readable; older ones
may still list `fiber_wavelength_mask_support`. Master Science algorithm version
2.0 gives new plans a distinct computation identity and publishes only the
detector image. No migration deletes old payloads automatically. Rebuilding the
new extracted-spectrum and mask products establishes their explicit trace and
wavelength provenance.

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
