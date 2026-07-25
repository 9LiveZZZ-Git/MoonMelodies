"""Phase 3 (API boundary) tests.

Cover the whitelist mapper, request validation, the result/JSON serializer, the schema
payload, and the ppworker plumbing (with a fake engine so no heavy pipeline runs). The
full bit-for-bit worker<->CLI parity is exercised by the engine-backed integration run
documented in the Phase 3 commit; these unit tests stay fast and are skipped wherever
the package cannot be imported.
"""
import os
import json

try:
    import pytest
except ImportError:
    pytest = None

try:
    from PlanetProfile.API import mapper, validate, results, schema, ppworker
    from PlanetProfile.Utilities.defineStructs import PlanetStruct
    _IMPORTED = True
except Exception:
    _IMPORTED = False


def _skip_if_no_pkg():
    if not _IMPORTED:
        if pytest is not None:
            pytest.skip('PlanetProfile not importable in this environment')
        return True
    return False


# --------------------------------------------------------------------------- validation

def test_validate_two_of_three():
    if _skip_if_no_pkg():
        return
    good = {'body': 'Europa', 'bulk': {'Tb_K': 268.3},
            'ocean': {'comp': 'Seawater', 'wOcean_ppt': 35.0}}
    assert validate.validate_request(good) == []                 # exactly two set -> OK
    three = {'body': 'Europa', 'bulk': {'Tb_K': 268.3, 'zb_approximate_km': 30.0},
             'ocean': {'wOcean_ppt': 35.0}}
    assert any('two of' in e['message'] for e in validate.validate_request(three))
    one = {'body': 'Europa', 'bulk': {'Tb_K': 268.3}}
    assert validate.validate_request(one)                        # only one set -> error


def test_validate_body_and_comp_enums():
    if _skip_if_no_pkg():
        return
    base = {'bulk': {'Tb_K': 268.3}, 'ocean': {'wOcean_ppt': 35.0}}
    assert any(e['field'] == 'body' for e in validate.validate_request({**base, 'body': 'Xena'}))
    assert any(e['field'] == 'ocean.comp'
               for e in validate.validate_request({**base, 'body': 'Europa',
                                                   'ocean': {'comp': 'Brine', 'wOcean_ppt': 35.0}}))
    # CustomSolution* is accepted by prefix.
    ok = {'body': 'Europa', 'bulk': {'Tb_K': 268.3},
          'ocean': {'comp': 'CustomSolutionNaCl', 'wOcean_ppt': 35.0}}
    assert not any(e['field'] == 'ocean.comp' for e in validate.validate_request(ok))


def test_validate_no_h2o_requires_qsurf():
    if _skip_if_no_pkg():
        return
    assert any(e['field'] == 'bulk.qSurf_Wm2'
               for e in validate.validate_request({'body': 'Io', 'do': {'NO_H2O': True}}))
    ok = {'body': 'Io', 'do': {'NO_H2O': True}, 'bulk': {'qSurf_Wm2': 0.1}}
    assert not any(e['field'] == 'bulk.qSurf_Wm2' for e in validate.validate_request(ok))


def test_validate_eos_path_traversal():
    if _skip_if_no_pkg():
        return
    spec = {'body': 'Europa', 'bulk': {'Tb_K': 268.3}, 'ocean': {'wOcean_ppt': 35.0},
            'sil': {'mantleEOS': '../secret.tab'}}
    assert any(e['field'] == 'sil.mantleEOS' for e in validate.validate_request(spec))


# ------------------------------------------------------------------------------- mapper

def test_mapper_rejects_unknown_attribute():
    if _skip_if_no_pkg():
        return
    try:
        mapper.build_planet({'body': 'Europa', 'bulk': {'not_a_real_attr': 1}}, strict=True)
        assert False, 'unknown attribute was not rejected'
    except mapper.MappingError as e:
        assert e.errors[0]['field'] == 'bulk.not_a_real_attr'


def test_mapper_rejects_eos_object_holder():
    if _skip_if_no_pkg():
        return
    # Ocean.EOS holds an EOS object and must never be settable from JSON.
    try:
        mapper.build_planet({'body': 'Europa', 'ocean': {'EOS': 'x'}}, strict=True)
        assert False, 'EOS holder was not rejected'
    except mapper.MappingError as e:
        assert any(f['field'] == 'ocean.EOS' for f in e.errors)


def test_mapper_sets_whitelisted_values():
    if _skip_if_no_pkg():
        return
    P = mapper.build_planet({'body': 'Europa',
                             'bulk': {'Tb_K': 268.305, 'R_m': 1560.8e3},
                             'ocean': {'comp': 'Seawater', 'wOcean_ppt': 35.0},
                             'do': {'Fe_CORE': True},
                             'planet': {'PfreezeUpper_MPa': 150}})
    assert P.Bulk.Tb_K == 268.305 and P.Bulk.R_m == 1560.8e3
    assert P.Ocean.comp == 'Seawater' and P.Ocean.wOcean_ppt == 35.0
    assert P.Do.Fe_CORE is True
    assert P.PfreezeUpper_MPa == 150            # top-level scalar applied


def test_planet_to_spec_roundtrip_captures_top_level():
    if _skip_if_no_pkg():
        return
    # A Planet whose top-level search bound differs from the default must round-trip it,
    # else an iterative solve silently shifts (the bug the parity test caught).
    P = PlanetStruct('Europa')
    P.Bulk.Tb_K = 268.3
    P.Ocean.comp = 'Seawater'
    P.Ocean.wOcean_ppt = 35.0
    P.PfreezeUpper_MPa = 150                    # non-default (default is 230)
    spec = mapper.planet_to_spec(P)
    assert spec.get('planet', {}).get('PfreezeUpper_MPa') == 150
    P2 = mapper.build_planet(spec)
    assert P2.PfreezeUpper_MPa == 150
    assert P2.Bulk.Tb_K == 268.3 and P2.Ocean.wOcean_ppt == 35.0


def test_apply_run_flags_defaults_and_mode():
    if _skip_if_no_pkg():
        return
    from types import SimpleNamespace
    # apply_run_flags only *sets* flags on a deepcopy; a bare namespace stands in for Params
    # so the test needs no seeded UserConfigs/GetConfig.
    P = mapper.apply_run_flags({'mode': 'single',
                                'run': {'calcInduction': False, 'calcGravity': False}},
                               SimpleNamespace())
    assert P.SKIP_PLOTS is True                 # default: no server-side plotting
    assert P.SKIP_INDUCTION is True and P.SKIP_GRAVITY is True
    assert not (P.DO_EXPLOREOGRAM or P.DO_INDUCTOGRAM or P.DO_MONTECARLO)
    assert P.CALC_NEW is True


# ------------------------------------------------------------------------- serialization

def test_json_encoder_handles_complex_and_nonfinite():
    if _skip_if_no_pkg():
        return
    import numpy as np
    obj = {'c': complex(1.0, -2.0), 'nan': float('nan'), 'inf': np.inf,
           'arr': np.array([1.0, np.nan, 3.0]), 'i': np.int64(7)}
    s = json.dumps(results.to_jsonable(obj), allow_nan=False)   # strict JSON, no NaN
    back = json.loads(s)
    assert back['c'] == {'re': 1.0, 'im': -2.0}
    assert back['nan'] is None and back['inf'] is None
    assert back['arr'] == [1.0, None, 3.0]
    assert back['i'] == 7


def test_schema_payload_is_strict_json():
    if _skip_if_no_pkg():
        return
    payload = schema.schema_payload()
    json.dumps(payload, allow_nan=False)                        # must not raise on NaN/Inf
    assert payload['enums']['body']                             # bodies enumerated
    assert 'bulk' in payload['inputSchema']['properties']
    assert 'phaseLegend' in payload['outputDictionary']


# ------------------------------------------------------------------------------- worker

class _FakeEngine:
    """ Stand-in for PlanetProfile.Main: 'runs' by returning the Planet unchanged, so the
        harness plumbing (validate -> build -> extract -> result.json -> manifest) is tested
        without the heavy pipeline. """
    @staticmethod
    def PlanetProfile(Planet, Params):
        return Planet, Params


def test_worker_run_job_plumbing(tmp_path=None):
    if _skip_if_no_pkg():
        return
    import tempfile
    from types import SimpleNamespace
    cp = SimpleNamespace()   # fake pristine Params; the fake engine never reads it
    jobdir = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp()
    spec = {'body': 'Europa', 'bulk': {'Tb_K': 268.3},
            'ocean': {'comp': 'Seawater', 'wOcean_ppt': 35.0},
            'run': {'calcInduction': False, 'calcGravity': False, 'noSaveFile': True}}
    term = ppworker.run_job(spec, jobdir, 'unit1', _FakeEngine, cp, emit=lambda o: None)
    assert term['status'] in ('succeeded', 'invalid')
    # result.json is written and is strict-parseable.
    with open(os.path.join(jobdir, 'result.json')) as f:
        result = json.load(f)
    assert result['meta']['body'] == 'Europa'
    assert 'summary' in result and 'layers' in result and 'gravity' in result
    # A validation failure returns a structured error and never raises.
    bad = ppworker.run_job({'body': 'Nope'}, jobdir, 'unit2', _FakeEngine, cp, emit=lambda o: None)
    assert bad['status'] == 'failed' and bad['error']['code'] == 'invalid_request'


if __name__ == '__main__':
    ok = 0
    fail = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                ok += 1
                print(f'PASS {name}')
            except Exception as e:
                fail += 1
                print(f'FAIL {name}: {type(e).__name__}: {e}')
    print(f'\n{ok} passed, {fail} failed')
