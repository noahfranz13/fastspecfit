#!/usr/bin/env python
"""
fastspecfit.external.desiqa
===========================


"""
import pdb # for debugging

import os, sys, time
import numpy as np

from desiutil.log import get_logger
log = get_logger()

# ridiculousness!
import tempfile
os.environ['MPLCONFIGDIR'] = tempfile.mkdtemp()

import matplotlib
matplotlib.use('Agg')

def parse(options=None):
    """Parse input arguments.

    """
    import argparse

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    # required but with sensible defaults
    parser.add_argument('--night', default='20200225', type=str, help='Night to process.')
    parser.add_argument('--tile', default='70502', type=str, help='Tile number to process.')

    # optional inputs
    parser.add_argument('--first', type=int, help='Index of first spectrum to process (0-indexed).')
    parser.add_argument('--last', type=int, help='Index of last spectrum to process (max of nobj-1).')
    parser.add_argument('--nproc', default=1, type=int, help='Number of cores.')
    parser.add_argument('--specprod', type=str, default='variance-model', choices=['andes', 'daily', 'variance-model'],
                        help='Spectroscopic production to process.')

    parser.add_argument('--use-vi', action='store_true', help='Select spectra with high-quality visual inspections (VI).')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite any existing files.')
    parser.add_argument('--no-write-spectra', dest='write_spectra', default=True, action='store_false',
                        help='Do not write out the selected spectra for the specified tile and night.')
    parser.add_argument('--verbose', action='store_true', help='Be verbose.')

    if options is None:
        args = parser.parse_args()
        log.info(' '.join(sys.argv))
    else:
        args = parser.parse_args(options)
        log.info('fastspecfit_qa {}'.format(' '.join(options)))

    return args

def main(args=None, comm=None):
    """Main module.

    """
    from astropy.table import Table
    from fastspecfit.continuum import ContinuumFit
    from fastspecfit.emlines import EMLineFit
    from fastspecfit.external.desi import read_spectra, unpack_all_spectra

    if isinstance(args, (list, tuple, type(None))):
        args = parse(args)

    fastspecfit_dir = os.getenv('FASTSPECFIT_DATA')
    resultsdir = os.path.join(fastspecfit_dir, 'results', args.specprod)
    qadir = os.path.join(fastspecfit_dir, 'qa', args.specprod)

    specfitfile = os.path.join(resultsdir, 'specfit-{}-{}.fits'.format(
        args.tile, args.night))
    photfitfile = os.path.join(resultsdir, 'photfit-{}-{}.fits'.format(
        prefix, args.tile, args.night))
    if not os.path.isfile(specfitfile):
        log.info('Spectroscopic fit file {} not found!'.format(specfitfile))
        return
    if not os.path.isfile(photfitfile):
        log.info('Photometric fit file {} not found!'.format(photfitfile))
        return
    specfit = Table.read(specfitfile)
    photfit = Table.read(photfitfile)
    log.info('Read {} objects from {}'.format(len(fastspecfit), fastspecfitfile))

    # Read the data 
    zbest, specobj = read_spectra(tile=args.tile, night=args.night,
                                  specprod=args.specprod,
                                  use_vi=args.use_vi, 
                                  write_spectra=args.write_spectra,
                                  verbose=args.verbose)

    if args.first is None:
        args.first = 0
    if args.last is None:
        args.last = len(zbest) - 1
    fitindx = np.arange(args.last - args.first + 1) + args.first

    # Initialize the continuum- and emission-line fitting classes.
    CFit = ContinuumFit(nproc=args.nproc, verbose=args.verbose)
    EMFit = EMLineFit()

    # Unpacking with multiprocessing takes a lot longer (maybe pickling takes a
    # long time?) so suppress the `nproc` argument here for now.
    data = unpack_all_spectra(specobj, zbest, CFit, fitindx)#, nproc=args.nproc)
    del specobj, zbest # free memory

    for iobj, indx in enumerate(fitindx):
        continuum = CFit.qa_continuum(data[iobj], specfit[indx], photfit[indx], qadir=qadir)
        EMFit.qa_emlines(data[iobj], specfit[indx], continuum, qadir=qadir)
