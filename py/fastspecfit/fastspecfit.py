#!/usr/bin/env python
"""
fastspecfit.fastspecfit
=======================

FastSpecFit wrapper for DESI.

"""
import pdb # for debugging

import os, time
import numpy as np

from desiutil.log import get_logger
log = get_logger()

# ridiculousness! - this seems to come from healpy, blarg
import tempfile
os.environ['MPLCONFIGDIR'] = tempfile.mkdtemp()

def _fastspecfit_one(args):
    """Multiprocessing wrapper."""
    return fastspecfit_one(*args)

def fastspecfit_one(iobj, data, CFit, EMFit, out, fastphot=False, solve_vdisp=False):
    """Fit one spectrum."""
    #log.info('Continuum-fitting object {}'.format(iobj))
    t0 = time.time()
    if fastphot:
        cfit, _ = CFit.continuum_fastphot(data)
    else:
        cfit, continuummodel = CFit.continuum_specfit(data, solve_vdisp=solve_vdisp)
    for col in cfit.colnames:
        out[col] = cfit[col]
    log.info('Continuum-fitting object {} took {:.2f} sec'.format(iobj, time.time()-t0))
    
    if fastphot:
        return out

    # fit the emission-line spectrum
    t0 = time.time()
    emfit = EMFit.fit(data, continuummodel)
    for col in emfit.colnames:
        out[col] = emfit[col]
    log.info('Line-fitting object {} took {:.2f} sec'.format(iobj, time.time()-t0))
        
    return out

def parse(options=None):
    """Parse input arguments.

    """
    import argparse, sys

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('-n', '--ntargets', type=int, help='Number of targets to process in each file.')
    parser.add_argument('--firsttarget', type=int, default=0, help='Index of first object to to process in each file (0-indexed).')
    parser.add_argument('--targetids', type=str, default=None, help='Comma-separated list of target IDs to process.')
    parser.add_argument('--mp', type=int, default=1, help='Number of multiprocessing processes per MPI rank or node.')
    #parser.add_argument('--suffix', type=str, default=None, help='Optional suffix for output filename.')
    parser.add_argument('-o', '--outfile', type=str, required=True, help='Full path to output filename.')

    parser.add_argument('--exposures', action='store_true', help='Fit the individual exposures (not the coadds).')

    parser.add_argument('--qa', action='store_true', help='Build QA (skips fitting).')
    parser.add_argument('--fastphot', action='store_true', help='Fit just the broadband photometry.')
    parser.add_argument('--solve-vdisp', action='store_true', help='Solve for the velocity disperion.')

    parser.add_argument('zbestfiles', nargs='*', help='Full path to input zbest file(s).')

    if options is None:
        args = parser.parse_args()
        log.info(' '.join(sys.argv))
    else:
        args = parser.parse_args(options)
        log.info('fastspecfit {}'.format(' '.join(options)))

    return args

def main(args=None, comm=None):
    """Main module.

    """
    from astropy.table import Table
    from fastspecfit.continuum import ContinuumFit
    from fastspecfit.emlines import EMLineFit
    from fastspecfit.io import DESISpectra, write_fastspec

    if isinstance(args, (list, tuple, type(None))):
        args = parse(args)

    if args.targetids:
        targetids = [int(x) for x in args.targetids.split(',')]
    else:
        targetids = args.targetids

    # Initialize the continuum- and emission-line fitting classes.
    t0 = time.time()
    CFit = ContinuumFit()
    EMFit = EMLineFit()
    Spec = DESISpectra()
    log.info('Initializing the classes took: {:.2f} sec'.format(time.time()-t0))

    # Read the data.
    t0 = time.time()
    Spec.find_specfiles(args.zbestfiles, exposures=args.exposures, firsttarget=args.firsttarget,
                        targetids=targetids, ntargets=args.ntargets)
    if len(Spec.specfiles) == 0:
        return
    data = Spec.read_and_unpack(CFit, exposures=args.exposures, fastphot=args.fastphot,
                                synthphot=True)
    
    out = Spec.init_output(CFit, EMFit, fastphot=args.fastphot)
    log.info('Reading and unpacking the {} spectra to be fitted took: {:.2f} sec'.format(
        Spec.ntargets, time.time()-t0))

    # Fit in parallel
    t0 = time.time()
    fitargs = [(iobj, data[iobj], CFit, EMFit, out[iobj], args.fastphot, args.solve_vdisp)
               for iobj in np.arange(Spec.ntargets)]
    if args.mp > 1:
        import multiprocessing
        with multiprocessing.Pool(args.mp) as P:
            _out = P.map(_fastspecfit_one, fitargs)
    else:
        _out = [fastspecfit_one(*_fitargs) for _fitargs in fitargs]
    out = Table(np.hstack(_out))
    log.info('Fitting everything took: {:.2f} sec'.format(time.time()-t0))

    # Write out.
    write_fastspec(out, outfile=args.outfile, specprod=Spec.specprod)
