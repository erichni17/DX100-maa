"""Fixtures and independent oracle derived from Joshveer Grewal's ArchEvolve
UME audit (main commit 8ec862e). Selected function bodies retain the upstream
semantics; see upstream-provenance.json. No compiler/run is started on import.
"""
from array import array
import math, struct, json
from pathlib import Path
INPUT_TYPES = {'c_to_p_map': 'i', 'c_to_z_map': 'i', 'point_type': 'i', 'corner_volume': 'f', 'csurf': 'f', 'zone_field': 'f', 'point_normal': 'f'}
CSR_NAMES = ('p_to_c_indptr', 'p_to_c_indices', 'z_to_c_indptr', 'z_to_c_indices')

def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]

def divide(a, b):
    return a / b if b else math.nan if not a else math.copysign(math.inf, a)

def fixtures():

    def make(name, np, pmap, types=None, seed=False):
        nz = 5
        nc = len(pmap)
        zmap = [(3 * c + 2) % nz for c in range(nc)]
        counts = [0] * np
        for p in pmap:
            counts[p] += 1
        types = types if types is not None else [0 if counts[p] == 0 else [1, -1, 0, 2][p % 4] for p in range(np)]
        return dict(id=name, points=np, zones=nz, corners=nc, c_to_p_map=pmap, c_to_z_map=zmap, point_type=types, corner_volume=[f32(0.5 + 0.25 * (c % 7)) for c in range(nc)], csurf=[f32(-0.75 + 0.25 * (c % 9)) for c in range(nc)], zone_field=[f32(1.25 + 0.5 * z) for z in range(nz)], point_normal=[f32(0.25 + 0.125 * (p % 5)) for p in range(np)], initial_point_volume=[1.0 if seed else 0.0] * np, initial_point_gradient=[-2.0 if seed else 0.0] * np, expected_strict_pass=not (seed or name == 'stock-style-empty-normalization'), scope='Deterministic diagnostic inputs; unchanged preprocessing/kernels; not the stock random driver.')
    rows = [make('mixed-rows', 7, [3, 1, 5, 1, 0, 3, 5, 3, 1, 5, 0], [1, -1, 0, 0, 0, 2, 0]), make('zero-points', 0, []), make('empty-non-normalizing-rows', 4, [], [0, -2, 0, -2]), make('one-internal', 1, [0], [2]), make('one-boundary', 1, [0], [-1]), make('exact-long-row', 3, [1] * 16384, [0, 1, 0]), make('long-row-tail-one', 3, [1] * 16385, [0, 1, 0]), make('outer-point-tail', 16385, [0, 16384, 16383, 0, 16384, 2, 16382])]
    rows += [make('stock-style-empty-normalization', 5, [0, 0], [1, 1, -1, 1, -1]), make('nonzero-initial-output', 7, [3, 1, 5, 1, 0, 3, 5, 3, 1, 5, 0], [1, -1, 0, 0, 0, 2, 0], True)]
    return rows

def inverse(values, n):
    rows = [[] for _ in range(n)]
    for (c, x) in enumerate(values):
        rows[x].append(c)
    ptr = [0]
    indices = []
    for row in rows:
        indices += row
        ptr.append(len(indices))
    return (ptr, indices)

def csr_expected(c):
    (pp, pi) = inverse(c['c_to_p_map'], c['points'])
    (zp, zi) = inverse(c['c_to_z_map'], c['zones'])
    return dict(p_to_c_indptr=pp, p_to_c_indices=pi, z_to_c_indptr=zp, z_to_c_indices=zi)

def oracle(c, actual_initial=False):
    volume = c['initial_point_volume'].copy() if actual_initial else [0.0] * c['points']
    gradient = c['initial_point_gradient'].copy() if actual_initial else [0.0] * c['points']
    csr = csr_expected(c)
    for p in range(c['points']):
        for k in range(csr['p_to_c_indptr'][p], csr['p_to_c_indptr'][p + 1]):
            corner = csr['p_to_c_indices'][k]
            z = c['c_to_z_map'][corner]
            volume[p] = f32(volume[p] + c['corner_volume'][corner])
            gradient[p] = f32(gradient[p] + f32(c['csurf'][corner] * c['zone_field'][z]))
    for (p, t) in enumerate(c['point_type']):
        if t > 0:
            gradient[p] = f32(divide(gradient[p], volume[p]))
        elif t == -1:
            ppdot = float(f32(gradient[p] * c['point_normal'][p]))
            gradient[p] = f32(divide(float(gradient[p]) - float(c['point_normal'][p]) * ppdot, float(volume[p])))
    return {'point_volume': volume, 'point_gradient': gradient}

def array_values(p, typ='f'):
    a = array(typ)
    a.frombytes(p.read_bytes())
    return list(a)

def matches(a, b):
    return len(a) == len(b) and all((math.isnan(x) and math.isnan(y) or struct.pack('<f', x) == struct.pack('<f', y) for (x, y) in zip(a, b)))

def coverage(c):
    csr = csr_expected(c)
    ptr = csr['p_to_c_indptr']
    chunks = []
    for p in range(0, c['points'], 16384):
        total = ptr[min(p + 16384, c['points'])] - ptr[p]
        chunks.append({'point_base': p, 'point_count': min(16384, c['points'] - p), 'corners': total, 'inner_chunks': (total + 16383) // 16384, 'last_inner_length': total % 16384 or (16384 if total else 0)})
    return {'point_blocks': chunks, 'empty_rows': sum((ptr[p] == ptr[p + 1] for p in range(c['points']))), 'max_row_length': max((ptr[p + 1] - ptr[p] for p in range(c['points'])), default=0), 'inverse_order_is_nonidentity': csr['p_to_c_indices'] != list(range(c['corners'])), 'normalization_counts': {'internal': sum((t > 0 for t in c['point_type'])), 'boundary': c['point_type'].count(-1), 'unchanged': sum((t <= 0 and t != -1 for t in c['point_type']))}, 'distinct_zone_targets': len(set(c['c_to_z_map']))}
TOLERANCE = 0.001
CONTROL_EXPECTATIONS = {'identical': True, 'within_tolerance': True, 'finite_mismatch': False, 'nan_actual': False, 'nan_expected': False, 'infinity_actual': False, 'infinity_expected': False, 'shape_mismatch': False}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def compare(expected, actual):
    result = dict(same_size=len(expected) == len(actual), compared=0, nonfinite=0, mismatches=0, max_absolute=0.0, max_relative=0.0, worst_absolute_index=-1, worst_relative_index=-1, relative_undefined=0)
    if result['same_size']:
        for (i, (a, b)) in enumerate(zip(expected, actual)):
            result['compared'] += 1
            if not math.isfinite(a) or not math.isfinite(b):
                result['nonfinite'] += 1
                continue
            error = abs(a - b)
            result['mismatches'] += error > TOLERANCE
            if error > result['max_absolute']:
                result.update(max_absolute=error, worst_absolute_index=i)
            if a:
                relative = error / abs(a)
                if relative > result['max_relative']:
                    result.update(max_relative=relative, worst_relative_index=i)
            elif error:
                result['relative_undefined'] += 1
    result['passed'] = result['same_size'] and (not result['nonfinite']) and (not result['mismatches'])
    return result

def validate_controls(document):
    rows = document['tests']
    require(len(rows) == len(CONTROL_EXPECTATIONS), 'Missing/duplicate checker controls')
    require({r['name'] for r in rows} == set(CONTROL_EXPECTATIONS), 'Wrong checker controls')
    for row in rows:
        expected = CONTROL_EXPECTATIONS[row['name']]
        require(row['expected_pass'] is expected and row['actual_pass'] is expected, 'Checker control failed: ' + row['name'])
    require(document['passed'] is True, 'Checker controls did not pass')

def validate_case(case, directory, exit_code, require_oracle):
    report = json.loads((directory / 'result.json').read_text())
    require(report['workers'] == 1, 'Wrong worker count')
    for key in ('points', 'zones', 'corners'):
        require(report[key] == case[key], 'Wrong case extent: ' + key)
    csr = csr_expected(case)
    for stage in ('before', 'after_baseline', 'after_maa'):
        for (name, typ) in INPUT_TYPES.items():
            require((directory / (stage + '.' + name + '.bin')).read_bytes() == array(typ, case[name]).tobytes(), 'Input changed: ' + name)
        for name in CSR_NAMES:
            require((directory / (stage + '.' + name + '.bin')).read_bytes() == array('i', csr[name]).tobytes(), 'CSR mismatch: ' + name)
    (baseline_reference, maa_reference) = (oracle(case), oracle(case, True))
    (checks, oracle_checks, nonfinite) = ({}, {}, {})
    for name in ('point_volume', 'point_gradient'):
        baseline = array_values(directory / ('baseline.' + name + '.bin'))
        maa = array_values(directory / ('maa.' + name + '.bin'))
        require(len(baseline) == len(maa) == case['points'], 'Output extent mismatch')
        checks[name] = compare(baseline, maa)
        require(checks[name] == report['comparisons'][name], 'Reported checker result differs from bytes')
        oracle_checks[name] = {'baseline_matches': matches(baseline, baseline_reference[name]), 'maa_matches': matches(maa, maa_reference[name])}
        require(oracle_checks[name]['baseline_matches'], 'CPU baseline differs from serial oracle')
        if require_oracle:
            require(oracle_checks[name]['maa_matches'], 'Repaired MAA differs from serial oracle')
        nonfinite[name] = {'baseline': sum((not math.isfinite(v) for v in baseline)), 'maa': sum((not math.isfinite(v) for v in maa))}
    passed = all((c['passed'] for c in checks.values()))
    require(report['strict_checker_passed'] is passed and exit_code == (0 if passed else 1), 'Checker/exit-code disagreement')
    return {'case': case['id'], 'strict_checker_passed': passed, 'expected_strict_pass': case['expected_strict_pass'], 'exit_code': exit_code, 'comparisons': checks, 'independent_oracle': oracle_checks, 'nonfinite_counts': nonfinite, 'inputs_unchanged': True, 'CSR_exact': True, 'coverage': coverage(case)}
