#!/usr/bin/env python3
"""Bounded native functional regression; no gem5 run or production-data read.

Compare original and repaired kernels with unchanged fixtures/checker/oracle
from the upstream audit. Build and run serially; preserve expected caller
failures and never assert that every compiler must expose the original bug.
"""
import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys

import fixture_model as model

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
BASE_REVISION = 'e4fc4afdf894f295442cef3604667a469fab8e62'
BASE_SHA = '13e7bef03c855bba53ff907e9454540be79b33a6757520f4f4e15c529d930d9c'
FIXED_SHA = 'cd1dc53143262be56b4767395fe7183597c82dc479bd90503863ecc4a45bcb19'
KERNEL = 'benchmarks/UME/gradzatp_invert.cpp'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dump(p, value):
    with Path(p).open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def run(args):
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 << 20, 256 << 20))
    if args.core is not None:
        assert args.core in os.sched_getaffinity(0)
        os.sched_setaffinity(0, {args.core})
    output = args.output.resolve()
    assert not output.exists(), 'use a fresh result directory'
    output.mkdir(parents=True, mode=0o700)
    env = {k:os.environ[k] for k in ('HOME','USER','LOGNAME') if k in os.environ}
    env.update(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8',
               OMP_NUM_THREADS='1', OMP_DYNAMIC='FALSE', OMP_THREAD_LIMIT='1')
    original = subprocess.check_output(['git','-C',str(REPO),'show',BASE_REVISION+':'+KERNEL])
    fixed = (REPO/KERNEL).read_bytes()
    assert hashlib.sha256(original).hexdigest() == BASE_SHA
    assert hashlib.sha256(fixed).hexdigest() == FIXED_SHA
    assert fixed == original.replace(b'#pragma omp simd simdlen(4)',
        b'// ordered-reduction-v1: repeated point IDs carry dependencies; preserve loop order.',1)
    provenance = json.loads((HERE/'upstream-provenance.json').read_text())
    for name in ('harness.cpp','strict_compare.hpp','checker_controls.cpp'):
        assert sha(HERE/name) == provenance['upstream_files'][name]['sha256']
    for edition, data in (('original', original), ('fixed', fixed)):
        stage = output/edition;stage.mkdir()
        (stage/'gradzatp_invert.cpp').write_bytes(data)
        for name in ('MAA.hpp','MAA_functional.hpp'):
            relative = 'benchmarks/API/'+name
            raw = (REPO/relative).read_bytes()
            assert raw == subprocess.check_output(['git','-C',str(REPO),'show',BASE_REVISION+':'+relative])
            (stage/name).write_bytes(raw)
        for name in ('harness.cpp','strict_compare.hpp','checker_controls.cpp'):
            shutil.copyfile(HERE/name, stage/name)
    cases = model.fixtures()
    assert len(cases) == 10 and sum(c['expected_strict_pass'] for c in cases) == 8
    dump(output/'cases.json', cases)
    for case in cases:
        d = output/'inputs'/case['id'];d.mkdir(parents=True)
        for name, kind in model.INPUT_TYPES.items():
            (d/(name+'.bin')).write_bytes(array(kind,case[name]).tobytes())
        for name in ('point_volume','point_gradient'):
            (d/('initial.'+name+'.bin')).write_bytes(array('f',case['initial_'+name]).tobytes())
    report = dict(schema='ume.gradzatp-invert.ordered-reduction.linux-functional.v1',
        host=dict(system=platform.system(), machine=platform.machine(), affinity=sorted(os.sched_getaffinity(0))),
        original_kernel_sha256=BASE_SHA, fixed_kernel_sha256=FIXED_SHA,
        compiler_profiles=[], cases=[], commands=[], checker_controls=[],
        simulation_executions=0, multiworker_or_hardware_validation=False,
        performance_qualified=False, original_artifacts_rewritten=False)

    def execute(argv, name, accepted=(0,)):
        stdout, stderr = output/(name+'.stdout'), output/(name+'.stderr')
        with stdout.open('xb') as out, stderr.open('xb') as err:
            result = subprocess.run(list(map(str,argv)), env=env, cwd=output, stdout=out, stderr=err)
        report['commands'].append(dict(argv=list(map(str,argv)),returncode=result.returncode,
                                       stdout=stdout.name,stderr=stderr.name))
        assert result.returncode in accepted, (name,result.returncode,stderr.read_text()[-3000:])
        return result.returncode

    for compiler_index, requested in enumerate(args.compiler or ['g++']):
        cc = Path(shutil.which(requested) or requested).resolve();assert cc.is_file()
        execute([cc,'--version'],f'compiler-{compiler_index}')
        version=(output/f'compiler-{compiler_index}.stdout').read_text().splitlines()[0]
        profiles=[('O2',[]),('O3',[])]
        if args.native_stress:
            profiles.append(('O3-native',['-march=native']))
        for profile, extra in profiles:
            tag=f'c{compiler_index}-{profile}'
            common=['-std=c++11','-'+profile.split('-')[0],'-g','-fopenmp',
                    '-fno-fast-math','-ffp-contract=off','-DFUNC','-DMAA','-DVERIFY',
                    '-DNUM_CORES=4','-DTILE_SIZE=16384',*extra]
            report['compiler_profiles'].append(dict(tag=tag,path=str(cc),sha256=sha(cc),
                version=version,flags=common,native_stress=bool(extra)))
            for edition in ('original','fixed'):
                stage=output/edition;binary=output/(tag+'-'+edition)
                flags=[*common,'-I',str(stage)]
                execute([cc,*flags,'-fopt-info-vec-all='+str(output/(tag+'-'+edition+'-vectorization.txt')),
                         stage/'harness.cpp','-o',binary],tag+'-'+edition+'-compile')
                for case in cases:
                    case_dir=output/tag/edition/case['id'];case_dir.mkdir(parents=True)
                    rc=execute([binary,output/'inputs'/case['id'],case_dir],
                               tag+'-'+edition+'-'+case['id'],accepted=(0,1))
                    row=model.validate_case(case,case_dir,rc,require_oracle=edition=='fixed')
                    row.update(profile=tag,edition=edition)
                    if edition=='fixed':
                        assert row['strict_checker_passed']==case['expected_strict_pass'],row
                        if case['expected_strict_pass']:
                            for name in ('point_volume','point_gradient'):
                                assert (case_dir/('baseline.'+name+'.bin')).read_bytes()==(case_dir/('maa.'+name+'.bin')).read_bytes(),(tag,case['id'],name)
                    report['cases'].append(row)
            control=output/(tag+'-checker-controls')
            execute([cc,*common,'-I',str(output/'fixed'),output/'fixed/checker_controls.cpp','-o',control],tag+'-controls-compile')
            execute([control],tag+'-controls')
            document=json.loads((output/(tag+'-controls.stdout')).read_text())
            model.validate_controls(document)
            report['checker_controls'].append(dict(profile=tag,tests=document['tests'],passed=True))
            assert sha(cc)==report['compiler_profiles'][-1]['sha256'], 'compiler changed during check'
    original_failures=[r for r in report['cases'] if r['edition']=='original' and r['expected_strict_pass'] and not r['strict_checker_passed']]
    fixed_finite=[r for r in report['cases'] if r['edition']=='fixed' and r['expected_strict_pass']]
    fixed_negative=[r for r in report['cases'] if r['edition']=='fixed' and not r['expected_strict_pass']]
    report.update(status='PASS_FIXED_EDITION_ALL_RECORDED_NATIVE_PROFILES',
        original_finite_strict_failures_reproduced=len(original_failures),
        original_failure_profiles=sorted({r['profile'] for r in original_failures}),
        fixed_finite_exact_passes=len(fixed_finite), expected_caller_failures_preserved=len(fixed_negative),
        max_individual_child_RSS_KiB=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        total_output_bytes=sum(p.stat().st_size for p in output.rglob('*') if p.is_file()))
    dump(output/'result.json', report)
    print(json.dumps({k:report[k] for k in ('status','original_finite_strict_failures_reproduced',
        'original_failure_profiles','fixed_finite_exact_passes','expected_caller_failures_preserved',
        'max_individual_child_RSS_KiB','total_output_bytes')},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--compiler',action='append')
    p.add_argument('--native-stress',action='store_true',help='extra host-ISA profile, separate from default compiler flags')
    p.add_argument('--core',type=int)
    run(p.parse_args())
