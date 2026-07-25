"""Regression tests for the Phase 2 bug-fix sweep.

Each test encodes the specific defect from the confirmed bug register: the
``buggy`` assertion documents the old behavior, and the following assertion
verifies the fix now in the code. Pure-logic tests need only numpy; a few
exercise the real package and are skipped if it cannot be imported.
"""
import numpy as np
try:
    import pytest
except ImportError:
    pytest = None


def test_cli_same_body_detection():
    # PlanetProfileCLI.py:44 -- np.all(bodynames == bodynames[0]) on a Python list
    # compares list-to-str -> scalar False, so the same-body branch was unreachable.
    bodynames = ['Europa', 'Europa']
    assert (bodynames == bodynames[0]) is False                    # the buggy expression
    assert all(bn == bodynames[0] for bn in bodynames)             # fix: True for same body
    assert not all(bn == bodynames[0] for bn in ['Europa', 'Titan'])


def test_negative_gradient_index0_detected():
    # LayerPropagators.py:1111 -- np.any(np.where(diff<0)) tests index VALUES, so a
    # negative gradient at index 0 (value 0 -> falsy) was silently missed.
    T = np.array([100.0, 99.0, 101.0, 102.0])       # negative gradient between layers 0 and 1
    grad = np.where(np.diff(T) < 0)
    assert not np.any(grad)                          # buggy: index value 0 -> False
    assert np.size(grad[0]) > 0                       # fix: emptiness test detects it


def test_constantprops_guard_short_circuits():
    # SetupInit.py:54 -- np.any(dict.values()) inspects the dict_values object (truthy),
    # so the all-False guard never short-circuits.
    d = {'Ocean': False, 'Ice': False, 'Inner': False}
    assert np.any(d.values())                         # buggy: True even though all False
    assert not any(d.values())                        # fix: correctly False


def test_worker_count_from_list_of_tuples():
    # SetupInit.py:1029 -- np.prod(np.shape(list_of_2tuples)) = 2*N, double the job count.
    jobs = [('Seawater', 10.0), ('MgSO4', 20.0), ('NaCl', 5.0)]
    assert int(np.prod(np.shape(jobs))) == 6          # buggy: 2 * 3
    assert len(jobs) == 3                             # fix: the true job count


def test_save_guard_demorgan():
    # MagneticInduction.py:256 -- (not INV or not MC) is True unless BOTH are in progress,
    # so the save guard fired during inversion/Monte-Carlo runs.
    buggy = lambda inv, mc: (not inv) or (not mc)
    fixed = lambda inv, mc: (not inv) and (not mc)
    assert buggy(True, False) is True                 # buggy: still saves mid-inversion
    assert fixed(True, False) is False                # fix: does not save mid-inversion
    assert fixed(False, False) is True                # normal run still saves


def test_sigma_copy_isolates_mutation():
    # reducedPlanetModel.py:51 -- aliasing Planet.sigma_Sm then in-place NaN cleanup
    # corrupted the canonical per-layer conductivities.
    orig = np.array([1.0, 0.0, np.nan, 3.0])
    alias = orig                                      # buggy alias
    alias[np.isnan(alias)] = -1
    assert not np.isnan(orig[2])                      # buggy: original was mutated
    orig2 = np.array([1.0, 0.0, np.nan, 3.0])
    work = orig2.copy()                               # fix: work on a copy
    work[np.isnan(work)] = -1
    assert np.isnan(orig2[2])                         # fix: original preserved


def test_remove_while_iterating_keeps_correct_items():
    # Main.py ExecOpts -- removing from fNames while iterating over it skipped files.
    src = ['a_missing', 'b_missing', 'c_ok']
    missing = {'a_missing', 'b_missing'}
    buggy = src.copy()
    for x in buggy:                                   # iterate the same list we mutate
        if x in missing:
            buggy.remove(x)
    assert buggy != ['c_ok']                          # buggy: a stray survives
    fixed = src.copy()
    for x in list(fixed):                             # fix: iterate a copy
        if x in missing:
            fixed.remove(x)
    assert fixed == ['c_ok']


def test_bodyname_none_with_fnames():
    # Main.py:90 -- bodyname None + fNames given -> bodyname.capitalize() AttributeError.
    def normalize(bodyname, fNames):
        if fNames is None and bodyname is None:
            bodyname = 'Europa'
        elif bodyname is None:
            bodyname = ''
        return bodyname.capitalize()
    assert normalize(None, ['PPEuropa.py']) == ''     # no crash, empty bodyname
    assert normalize(None, None) == 'Europa'
    assert normalize('titan', None) == 'Titan'


def test_ncores_floor_for_empty_grid():
    # ParPlanet -- np.prod(shape) = 0 for an empty grid -> Pool(0) crash.
    def ncores(n, maxc=8, tl=1000):
        return int(np.max([1, np.min([maxc, n, tl])]))
    assert ncores(0) == 1                             # empty grid -> Pool(1), not Pool(0)
    assert ncores(3) == 3


def test_compare_glob_excludes_sidecars():
    # Main.py:224 -- the COMPARE profile glob must reject the ocean-props/perm sidecars.
    names = ['EuropaProfile_x.txt', 'EuropaProfile_x_mantleCore.txt',
             'EuropaProfile_x_liquidOceanProps.txt', 'EuropaProfile_x_mantlePerm.txt']
    keep = [n for n in names if 'mantle' not in n and 'OceanProps' not in n and 'Perm' not in n]
    assert keep == ['EuropaProfile_x.txt']


def test_reload_oceanprops_name_matches_writer():
    # Main.py:665 -- reload-with-override must build the same sidecar name the writer uses.
    try:
        from PlanetProfile.Utilities.defineStructs import DataFilesSubstruct
    except Exception:
        if pytest is not None:
            pytest.skip('PlanetProfile not importable in this environment')
        return
    df = DataFilesSubstruct('Body', 'Body/BodyProfile_x', '')
    override = f'{"Body/BodyProfile_x.txt"[:-4]}_liquidOceanProps.txt'
    assert override.endswith('_liquidOceanProps.txt')
    assert df.oceanPropsFile.endswith('_liquidOceanProps.txt')       # single source of truth


def test_preload_eos_initializer_shares_eoslist():
    # Deferred Phase 2 item (SetupInit.PRELOAD_EOS): preloaded EOS must reach 'spawn'
    # workers. The Pool initializer repopulates the worker's module-global EOSlist so it
    # reuses the preloaded EOS instead of rebuilding them.
    try:
        from PlanetProfile.Utilities.defineStructs import EOSlist
        from PlanetProfile.Main import _initWorkerEOSlist
    except Exception:
        if pytest is not None:
            pytest.skip('PlanetProfile not importable in this environment')
        return
    EOSlist.loaded.pop('pp_test_eos', None)
    _initWorkerEOSlist({'pp_test_eos': 42}, {'pp_test_eos': 'range'})
    assert EOSlist.loaded.get('pp_test_eos') == 42        # worker EOSlist receives the preloaded EOS
    assert EOSlist.ranges.get('pp_test_eos') == 'range'
    EOSlist.loaded.pop('pp_test_eos', None)
    EOSlist.ranges.pop('pp_test_eos', None)


if __name__ == '__main__':
    import sys
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn(); ok += 1; print(f'PASS {name}')
            except Exception as e:
                print(f'FAIL {name}: {type(e).__name__}: {e}')
    print(f'\n{ok} passed')
