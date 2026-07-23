from pathlib import Path

from virusflow.qa.engine import QAEngine


def write_yaml(tmp_path: Path, text: str) -> str:
    p = tmp_path / "qa.yml"
    p.write_text(text)
    return str(p)


def test_identity_comparisons_is_not_none_pass(tmp_path):
    # YAML uses an identity comparison; this previously failed before engine supported `is`/`is not`.
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  foo:\n"
        "    policy: soft\n"
        "    metrics:\n"
        "      m: { from: meta.x }\n"
        "    checks:\n"
        "      - id: m_valid\n"
        "        where: 'm is not None and m > 0'\n"
        "        severity: fail_if_false\n"
    )
    ypath = write_yaml(tmp_path, yaml_text)
    eng = QAEngine(ypath)

    # Value present and positive → pass
    d_ok = eng.evaluate(kind="foo", meta={"x": 3.0})
    assert d_ok.status == "pass"

    # None → fail (violates is not None and > 0)
    d_fail = eng.evaluate(kind="foo", meta={"x": None})
    assert d_fail.status == "fail"

    # Zero → fail (second clause false)
    d_zero = eng.evaluate(kind="foo", meta={"x": 0.0})
    assert d_zero.status == "fail"


def test_metric_defaults_applied_when_missing(tmp_path):
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  bias:\n"
        "    policy: soft\n"
        "    metrics:\n"
        "      read_noise: { from: meta.read_noise, default: 3.2 }\n"
        "    checks:\n"
        "      - id: rn_range\n"
        "        where: 'read_noise is not None and read_noise > 0 and read_noise < 6'\n"
        "        severity: fail_if_false\n"
    )
    ypath = write_yaml(tmp_path, yaml_text)
    eng = QAEngine(ypath)

    # No readnoise provided; default should be used and pass
    d = eng.evaluate(kind="bias", meta={})
    assert d.metrics.get("read_noise") == 3.2
    assert d.status == "pass"


def test_reducers_and_status_priority_warn_over_pass_fail_over_warn(tmp_path):
    # Configure wavelength-map rules: fail if median >= 1.0; warn if p95 >= 1.8
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  wavelength_map:\n"
        "    policy: hard\n"
        "    metrics:\n"
        "      rms_median: { from: reduce.median(meta.per_fiber_wavelength_residual_rms) }\n"
        "      rms_p95: { from: reduce.percentile(meta.per_fiber_wavelength_residual_rms, 95) }\n"
        "    checks:\n"
        "      - id: med_lt_1_0\n"
        "        where: 'rms_median is not None and rms_median < 1.0'\n"
        "        severity: fail_if_false\n"
        "      - id: tail_lt_1_8\n"
        "        where: 'rms_p95 is not None and rms_p95 < 1.8'\n"
        "        severity: warn_if_false\n"
    )
    ypath = write_yaml(tmp_path, yaml_text)
    eng = QAEngine(ypath)

    # Case 1: median passes (<1.0), but p95 >= 1.8 → status warn
    meta_warn = {"per_fiber_wavelength_residual_rms": [0.4, 0.8, 2.1, float("nan")]}  # p95 ~ 2.1
    d_warn = eng.evaluate(kind="wavelength_map", meta=meta_warn)
    assert d_warn.metrics["rms_median"] < 1.0
    assert d_warn.status == "warn"

    # Case 2: median fails (>=1.0) → status fail regardless of warn check
    meta_fail = {"per_fiber_wavelength_residual_rms": [1.2, 1.1, 0.9]}
    d_fail = eng.evaluate(kind="wavelength_map", meta=meta_fail)
    assert d_fail.metrics["rms_median"] >= 1.0
    assert d_fail.status == "fail"


def test_policy_off_short_circuits_to_pass(tmp_path):
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  flat:\n"
        "    policy: off\n"
        "    metrics:\n"
        "      bad: { from: meta.bad }\n"
        "    checks:\n"
        "      - id: always_fail\n"
        "        where: 'bad is None'\n"
        "        severity: fail_if_false\n"
    )
    ypath = write_yaml(tmp_path, yaml_text)
    eng = QAEngine(ypath)

    d = eng.evaluate(kind="flat", meta={"bad": 123})
    assert d.policy == "off"
    assert d.status == "pass"  # policy off short-circuits


def test_master_bias_like_rule_passes_for_readnoise_near_three(tmp_path):
    # Mirror the default rule under test: readnoise ~3 should PASS
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  master_bias:\n"
        "    policy: soft\n"
        "    metrics:\n"
        "      read_noise: { from: meta.read_noise }\n"
        "    checks:\n"
        "      - id: read_noise_valid\n"
        "        where: 'read_noise is not None and read_noise > 1e-4 and read_noise < 6.0'\n"
        "        severity: fail_if_false\n"
    )
    ypath = write_yaml(tmp_path, yaml_text)
    eng = QAEngine(ypath)

    d = eng.evaluate(kind="master_bias", meta={"read_noise": 3.0})
    assert d.status == "pass"

    d_hi = eng.evaluate(kind="master_bias", meta={"read_noise": 6.5})
    assert d_hi.status == "fail"

    d_none = eng.evaluate(kind="master_bias", meta={"read_noise": None})
    assert d_none.status == "fail"


def test_master_arc_not_all_zero_rule(tmp_path):
    # Fail master_arc if all values are zero (p95 == 0)
    yaml_text = (
        "version: 1\n"
        "kinds:\n"
        "  master_arc:\n"
        "    policy: soft\n"
        "    metrics:\n"
        "      p95: { from: meta.p95 }\n"
        "    checks:\n"
        "      - id: not_all_zero\n"
        "        where: 'p95 is not None and p95 > 0'\n"
        "        severity: fail_if_false\n"
    )
    ypath = write_yaml(tmp_path, yaml_text)
    eng = QAEngine(ypath)

    # p95 == 0 -> should fail
    d_fail = eng.evaluate(kind="master_arc", meta={"p95": 0.0})
    assert d_fail.status == "fail"

    # p95 > 0 -> should pass
    d_ok = eng.evaluate(kind="master_arc", meta={"p95": 123.4})
    assert d_ok.status == "pass"
