#!/usr/bin/env python
"""
fastspecfit.io
==============

Tools for reading DESI spectra and reading and writing fastspecfit files.

"""
import pdb # for debugging

import os, time
import numpy as np
import fitsio
from astropy.table import Table

from fastspecfit.util import TabulatedDESI

from desiutil.log import get_logger
log = get_logger()

# Default environment variables.
DESI_ROOT_NERSC = '/global/cfs/cdirs/desi'
DUST_DIR_NERSC = '/global/cfs/cdirs/cosmo/data/dust/v0_1'
DR9_DIR_NERSC = '/global/cfs/cdirs/desi/external/legacysurvey/dr9'
FTEMPLATES_DIR_NERSC = '/global/cfs/cdirs/desi/science/gqp/templates/fastspecfit'

# list of all possible targeting bit columns
TARGETINGBITS = {
    'fuji': ['CMX_TARGET', 'DESI_TARGET', 'BGS_TARGET', 'MWS_TARGET', 'SCND_TARGET',
             'SV1_DESI_TARGET', 'SV1_BGS_TARGET', 'SV1_MWS_TARGET',
             'SV2_DESI_TARGET', 'SV2_BGS_TARGET', 'SV2_MWS_TARGET',
             'SV3_DESI_TARGET', 'SV3_BGS_TARGET', 'SV3_MWS_TARGET',
             'SV1_SCND_TARGET', 'SV2_SCND_TARGET', 'SV3_SCND_TARGET'],
    'default': ['DESI_TARGET', 'BGS_TARGET', 'MWS_TARGET', 'SCND_TARGET'],
    }

# fibermap and exp_fibermap columns to read
FMCOLS = ['TARGETID', 'TARGET_RA', 'TARGET_DEC', 'COADD_FIBERSTATUS', 'OBJTYPE',
          'PHOTSYS', 'RELEASE', 'BRICKNAME', 'BRICKID', 'BRICK_OBJID',
          #'FIBERFLUX_G', 'FIBERFLUX_R', 'FIBERFLUX_Z', 
          #'FIBERTOTFLUX_G', 'FIBERTOTFLUX_R', 'FIBERTOTFLUX_Z', 
          #'FLUX_G', 'FLUX_R', 'FLUX_Z', 'FLUX_W1', 'FLUX_W2',
          #'FLUX_IVAR_G', 'FLUX_IVAR_R', 'FLUX_IVAR_Z', 'FLUX_IVAR_W1', 'FLUX_IVAR_W2'
          ]
#FMCOLS = ['TARGETID', 'TARGET_RA', 'TARGET_DEC', 'COADD_FIBERSTATUS', 'OBJTYPE']

EXPFMCOLS = {
    'perexp': ['TARGETID', 'TILEID', 'FIBER', 'EXPID'],
    'pernight': ['TARGETID', 'TILEID', 'FIBER'],
    'cumulative': ['TARGETID', 'TILEID', 'FIBER'],
    'healpix': ['TARGETID', 'TILEID'], # tileid will be an array
    'custom': ['TARGETID', 'TILEID'], # tileid will be an array
    }

# redshift columns to read
REDSHIFTCOLS = ['TARGETID', 'Z', 'ZWARN', 'SPECTYPE', 'DELTACHI2']

# tsnr columns to read
TSNR2COLS = ['TSNR2_BGS', 'TSNR2_LRG', 'TSNR2_ELG', 'TSNR2_QSO', 'TSNR2_LYA']

# quasarnet afterburner columns to read
QNCOLS = ['TARGETID', 'Z_NEW', 'IS_QSO_QN_NEW_RR', 'C_LYA', 'C_CIV',
          'C_CIII', 'C_MgII', 'C_Hbeta', 'C_Halpha']
QNLINES = ['C_LYA', 'C_CIV', 'C_CIII', 'C_MgII', 'C_Hbeta', 'C_Halpha']

# targeting and Tractor columns to read from disk
#TARGETCOLS = ['TARGETID', 'RA', 'DEC', 'FLUX_W3', 'FLUX_W4', 'FLUX_IVAR_W3', 'FLUX_IVAR_W4']
TARGETCOLS = ['TARGETID', 'RA', 'DEC',
              'RELEASE', 'LS_ID',
              #'PHOTSYS',
              'FIBERFLUX_G', 'FIBERFLUX_R', 'FIBERFLUX_Z', 
              'FIBERTOTFLUX_G', 'FIBERTOTFLUX_R', 'FIBERTOTFLUX_Z', 
              'FLUX_G', 'FLUX_R', 'FLUX_Z', 'FLUX_W1', 'FLUX_W2', 'FLUX_W3', 'FLUX_W4',
              'FLUX_IVAR_G', 'FLUX_IVAR_R', 'FLUX_IVAR_Z',
              'FLUX_IVAR_W1', 'FLUX_IVAR_W2', 'FLUX_IVAR_W3', 'FLUX_IVAR_W4']#,

FLUXNORM = 1e17 # flux normalization factor for all DESI spectra [erg/s/cm2/A]

# Taken from Redrock/0.15.4
class _ZWarningMask(object):
    SKY               = 2**0  #- sky fiber
    LITTLE_COVERAGE   = 2**1  #- too little wavelength coverage
    SMALL_DELTA_CHI2  = 2**2  #- chi-squared of best fit is too close to that of second best
    NEGATIVE_MODEL    = 2**3  #- synthetic spectrum is negative
    MANY_OUTLIERS     = 2**4  #- fraction of points more than 5 sigma away from best model is too large (>0.05)
    Z_FITLIMIT        = 2**5  #- chi-squared minimum at edge of the redshift fitting range
    NEGATIVE_EMISSION = 2**6  #- a QSO line exhibits negative emission, triggered only in QSO spectra, if  C_IV, C_III, Mg_II, H_beta, or H_alpha has LINEAREA + 3 * LINEAREA_ERR < 0
    UNPLUGGED         = 2**7  #- the fiber was unplugged/broken, so no spectrum obtained
    BAD_TARGET        = 2**8  #- catastrophically bad targeting data
    NODATA            = 2**9  #- No data for this fiber, e.g. because spectrograph was broken during this exposure (ivar=0 for all pixels)
    BAD_MINFIT        = 2**10 #- Bad parabola fit to the chi2 minimum
    POORDATA          = 2**11 #- Poor input data quality but try fitting anyway
ZWarningMask = _ZWarningMask()

def _unpack_one_spectrum(args):
    """Multiprocessing wrapper."""
    return unpack_one_spectrum(*args)

def unpack_one_spectrum(igal, specdata, meta, ebv, Filters, fastphot, synthphot):
    """Unpack the data for a single object and correct for Galactic extinction. Also
    flag pixels which may be affected by emission lines.

    """
    from desiutil.dust import mwdust_transmission, dust_transmission

    if specdata['photsys'] == 'S':
        filters = Filters.decam
        allfilters = Filters.decamwise
    else:
        filters = Filters.bassmzls
        allfilters = Filters.bassmzlswise

    RV = 3.1
        
    # Unpack the imaging photometry and correct for MW dust.
    
    # Do not match the Legacy Surveys here because we want the MW
    # dust extinction correction we apply to the spectra to be
    # self-consistent with how we correct the photometry for dust.
    meta['EBV'] = ebv
    if specdata['photsys'] != '':
        mw_transmission_flux = np.array([mwdust_transmission(ebv, band, specdata['photsys'], match_legacy_surveys=False) for band in Filters.bands])
        for band, mwdust in zip(Filters.bands, mw_transmission_flux):
            meta['MW_TRANSMISSION_{}'.format(band.upper())] = mwdust 
    else:
        #mw_transmission_fiberflux = np.ones(len(Filters.bands))
        mw_transmission_flux = 10**(-0.4 * ebv * RV * ext_odonnell(allfilters.effective_wavelengths.value, Rv=RV))

    maggies = np.zeros(len(Filters.bands))
    ivarmaggies = np.zeros(len(Filters.bands))
    for iband, band in enumerate(Filters.bands):
        maggies[iband] = meta['FLUX_{}'.format(band.upper())] / mw_transmission_flux[iband]
        ivarmaggies[iband] = meta['FLUX_IVAR_{}'.format(band.upper())] * mw_transmission_flux[iband]**2
        
    if not np.all(ivarmaggies >= 0):
        errmsg = 'Some ivarmaggies are negative!'
        log.critical(errmsg)
        raise ValueError(errmsg)
    
    specdata['phot'] = Filters.parse_photometry(
        Filters.bands, maggies=maggies, ivarmaggies=ivarmaggies, nanomaggies=True,
        lambda_eff=allfilters.effective_wavelengths.value,
        min_uncertainty=Filters.min_uncertainty, log=log)
    
    # fiber fluxes
    if specdata['photsys'] != '':                
        mw_transmission_fiberflux = np.array([mwdust_transmission(ebv, band, specdata['photsys']) for band in Filters.fiber_bands])
    else:
        #mw_transmission_fiberflux = np.ones(len(Filters.fiber_bands))
        mw_transmission_fiberflux = 10**(-0.4 * ebv * RV * ext_odonnell(filters.effective_wavelengths.value, Rv=RV))
    
    fibermaggies = np.zeros(len(Filters.fiber_bands))
    fibertotmaggies = np.zeros(len(Filters.fiber_bands))
    #ivarfibermaggies = np.zeros(len(Filters.fiber_bands))
    for iband, band in enumerate(Filters.fiber_bands):
        fibermaggies[iband] = meta['FIBERFLUX_{}'.format(band.upper())] / mw_transmission_fiberflux[iband]
        fibertotmaggies[iband] = meta['FIBERTOTFLUX_{}'.format(band.upper())] / mw_transmission_fiberflux[iband]
        #ivarfibermaggies[iband] = meta['FIBERTOTFLUX_IVAR_{}'.format(band.upper())] * mw_transmission_fiberflux[iband]**2
    
    specdata['fiberphot'] = Filters.parse_photometry(Filters.fiber_bands,
        maggies=fibermaggies, nanomaggies=True,
        lambda_eff=filters.effective_wavelengths.value, log=log)
    specdata['fibertotphot'] = Filters.parse_photometry(Filters.fiber_bands,
        maggies=fibertotmaggies, nanomaggies=True,
        lambda_eff=filters.effective_wavelengths.value, log=log)

    if not fastphot:
        specdata.update({'linemask': [], 'linemask_all': [], 'linename': [],
                         'linepix': [], 'contpix': [], 'snr': np.zeros(3, 'f4')})
    
        cameras, npixpercamera = [], []
        for icam, camera in enumerate(specdata['cameras']):
            # Check whether the camera is fully masked.
            if np.sum(specdata['ivar'][icam]) == 0:
                log.warning('Dropping fully masked camera {}'.format(camera))
            else:
                ivar = specdata['ivar'][icam]
                mask = specdata['mask'][icam]

                # always mask the first and last pixels
                mask[0] = 1
                mask[-1] = 1

                # In the pipeline, if mask!=0 that does not mean ivar==0, but we
                # want to be more aggressive about masking here.
                ivar[mask != 0] = 0

                if np.all(ivar == 0):
                    log.warning('Dropping fully masked camera {}'.format(camera))
                else:
                    # Compute the SNR before we correct for dust.
                    specdata['snr'][icam] = np.median(specdata['flux'][icam] * np.sqrt(ivar))
                    #mw_transmission_spec = 10**(-0.4 * ebv * RV * ext_odonnell(wave[camera], Rv=RV))
                    mw_transmission_spec = dust_transmission(specdata['wave'][icam], ebv, Rv=RV)
                    specdata['flux'][icam] /= mw_transmission_spec
                    specdata['ivar'][icam] *= mw_transmission_spec**2
            
                    cameras.append(camera)
                    npixpercamera.append(len(specdata['wave'][icam])) # number of pixels in this camera

        # Pre-compute some convenience variables for "un-hstacking"
        # an "hstacked" spectrum.
        specdata['cameras'] = cameras
        specdata['npixpercamera'] = npixpercamera
        
        ncam = len(specdata['cameras'])
        npixpercam = np.hstack([0, npixpercamera])
        specdata['camerapix'] = np.zeros((ncam, 2), np.int16)
        for icam in np.arange(ncam):
            specdata['camerapix'][icam, :] = [np.sum(npixpercam[:icam+1]), np.sum(npixpercam[:icam+2])]
                                
        # coadded spectrum
        coadd_linemask_dict = Filters.build_linemask(specdata['coadd_wave'], specdata['coadd_flux'],
                                                     specdata['coadd_ivar'], redshift=specdata['zredrock'],
                                                     linetable=Filters.linetable)
        specdata['coadd_linename'] = coadd_linemask_dict['linename']
        specdata['coadd_linepix'] = [np.where(lpix)[0] for lpix in coadd_linemask_dict['linepix']]
        specdata['coadd_contpix'] = [np.where(cpix)[0] for cpix in coadd_linemask_dict['contpix']]
    
        specdata['linesigma_narrow'] = coadd_linemask_dict['linesigma_narrow']
        specdata['linesigma_balmer'] = coadd_linemask_dict['linesigma_balmer']
        specdata['linesigma_uv'] = coadd_linemask_dict['linesigma_uv']
    
        specdata['linesigma_narrow_snr'] = coadd_linemask_dict['linesigma_narrow_snr']
        specdata['linesigma_balmer_snr'] = coadd_linemask_dict['linesigma_balmer_snr']
        specdata['linesigma_uv_snr'] = coadd_linemask_dict['linesigma_uv_snr']

        specdata['smoothsigma'] = coadd_linemask_dict['smoothsigma']
        
        # Map the pixels belonging to individual emission lines and
        # their local continuum back onto the original per-camera
        # spectra. These lists of arrays are used in
        # continuum.ContinnuumTools.smooth_continuum.
        for icam in np.arange(len(specdata['cameras'])):
            #specdata['smoothflux'].append(np.interp(specdata['wave'][icam], specdata['coadd_wave'], coadd_linemask_dict['smoothflux']))
            specdata['linemask'].append(np.interp(specdata['wave'][icam], specdata['coadd_wave'], coadd_linemask_dict['linemask']*1) > 0)
            specdata['linemask_all'].append(np.interp(specdata['wave'][icam], specdata['coadd_wave'], coadd_linemask_dict['linemask_all']*1) > 0)
            _linename, _linenpix, _contpix = [], [], []
            for ipix in np.arange(len(coadd_linemask_dict['linepix'])):
                I = np.interp(specdata['wave'][icam], specdata['coadd_wave'], coadd_linemask_dict['linepix'][ipix]*1) > 0
                J = np.interp(specdata['wave'][icam], specdata['coadd_wave'], coadd_linemask_dict['contpix'][ipix]*1) > 0
                if np.sum(I) > 3 and np.sum(J) > 3:
                    _linename.append(coadd_linemask_dict['linename'][ipix])
                    _linenpix.append(np.where(I)[0])
                    _contpix.append(np.where(J)[0])
            specdata['linename'].append(_linename)
            specdata['linepix'].append(_linenpix)
            specdata['contpix'].append(_contpix)
    
        #import matplotlib.pyplot as plt
        #plt.clf()
        #for ii in np.arange(3):
        #    plt.plot(specdata['wave'][ii], specdata['flux'][ii])
        #plt.plot(specdata['coadd_wave'], coadd_flux-2, alpha=0.6, color='k')
        #plt.xlim(5500, 6000)
        #plt.savefig('test.png')
    
        specdata.update({'coadd_linemask': coadd_linemask_dict['linemask'],
                         'coadd_linemask_all': coadd_linemask_dict['linemask_all']})
    
        # Optionally synthesize photometry from the coadded spectrum.
        if synthphot:
            padflux, padwave = filters.pad_spectrum(specdata['coadd_flux'], specdata['coadd_wave'], method='edge')
            synthmaggies = filters.get_ab_maggies(padflux / FLUXNORM, padwave)
            synthmaggies = synthmaggies.as_array().view('f8')
    
            # code to synthesize uncertainties from the variance spectrum
            #var, mask = _ivar2var(specdata['coadd_ivar'])
            #padvar, padwave = filters.pad_spectrum(var[mask], specdata['coadd_wave'][mask], method='edge')
            #synthvarmaggies = filters.get_ab_maggies(1e-17**2 * padvar, padwave)
            #synthivarmaggies = 1 / synthvarmaggies.as_array().view('f8')[:3] # keep just grz
    
            #specdata['synthphot'] = Filters.parse_photometry(Filters.bands,
            #    maggies=synthmaggies, lambda_eff=lambda_eff[:3],
            #    ivarmaggies=synthivarmaggies, nanomaggies=False, log=log)
    
            specdata['synthphot'] = Filters.parse_photometry(Filters.synth_bands,
                maggies=synthmaggies, nanomaggies=False,
                lambda_eff=filters.effective_wavelengths.value, log=log)

    return specdata, meta

class DESISpectra(TabulatedDESI):
    def __init__(self, redux_dir=None, fiberassign_dir=None, dr9dir=None, mapdir=None):
        """Class to read in DESI spectra and associated metadata.

        Parameters
        ----------
        redux_dir : str
            Full path to the location of the reduced DESI data. Optional and
            defaults to `$DESI_SPECTRO_REDUX`.
        
        fiberassign_dir : str
            Full path to the location of the fiberassign files. Optional and
            defaults to `$DESI_ROOT/target/fiberassign/tiles/trunk`.

        """
        super(DESISpectra, self).__init__()
        
        desi_root = os.environ.get('DESI_ROOT', DESI_ROOT_NERSC)

        if redux_dir is None:
            self.redux_dir = os.path.join(desi_root, 'spectro', 'redux')
        else:
            self.redux_dir = redux_dir
            
        if fiberassign_dir is None:
            self.fiberassign_dir = os.path.join(desi_root, 'target', 'fiberassign', 'tiles', 'trunk')
        else:
            self.fiberassign_dir = fiberassign_dir

        if dr9dir is None:
            self.dr9dir = os.environ.get('DR9_DIR', DR9_DIR_NERSC)
        else:
            self.dr9dir = dr9dir

        if mapdir is None:
            self.mapdir = os.path.join(os.environ.get('DUST_DIR', DUST_DIR_NERSC), 'maps')
        else:
            self.mapdir = mapdir
            
    @staticmethod
    def resolve(targets):
        """Resolve which targets are primary in imaging overlap regions.
    
        Parameters
        ----------
        targets : :class:`~numpy.ndarray`
            Rec array of targets. Must have columns "RA" and "DEC" and
            either "RELEASE" or "PHOTSYS" or "TARGETID".
    
        Returns
        -------
        :class:`~numpy.ndarray`
            The original target list trimmed to only objects from the "northern"
            photometry in the northern imaging area and objects from "southern"
            photometry in the southern imaging area.
        
        """
        import healpy as hp
        
        def _isonnorthphotsys(photsys):
            """ If the object is from the northen photometric system """
            # ADM explicitly checking for NoneType. In the past we have had bugs
            # ADM where we forgot to populate variables before passing them.
            if photsys is None:
                msg = "NoneType submitted to _isonnorthphotsys function"
                log.critical(msg)
                raise ValueError(msg)
        
            psftype = np.asarray(photsys)
            # ADM in Python3 these string literals become byte-like
            # ADM so to retain Python2 compatibility we need to check
            # ADM against both bytes and unicode.
            northern = ((photsys == 'N') | (photsys == b'N'))
        
            return northern
                
        # ADM retrieve the photometric system from the RELEASE.
        from desitarget.io import release_to_photsys, desitarget_resolve_dec
        if 'PHOTSYS' in targets.dtype.names:
            photsys = targets["PHOTSYS"]
        else:
            if 'RELEASE' in targets.dtype.names:
                photsys = release_to_photsys(targets["RELEASE"])
            else:
                _, _, release, _, _, _ = decode_targetid(targets["TARGETID"])
                photsys = release_to_photsys(release)
    
        # ADM a flag of which targets are from the 'N' photometry.
        photn = _isonnorthphotsys(photsys)
    
        # ADM grab the declination used to resolve targets.
        split = desitarget_resolve_dec()
    
        # ADM determine which targets are north of the Galactic plane. As
        # ADM a speed-up, bin in ~1 sq.deg. HEALPixels and determine
        # ADM which of those pixels are north of the Galactic plane.
        # ADM We should never be as close as ~1o to the plane.
        from desitarget.geomask import is_in_gal_box, pixarea2nside
        nside = pixarea2nside(1)
        theta, phi = np.radians(90-targets["DEC"]), np.radians(targets["RA"])
        pixnum = hp.ang2pix(nside, theta, phi, nest=True)
        # ADM find the pixels north of the Galactic plane...
        allpix = np.arange(hp.nside2npix(nside))
        theta, phi = hp.pix2ang(nside, allpix, nest=True)
        ra, dec = np.degrees(phi), 90-np.degrees(theta)
        pixn = is_in_gal_box([ra, dec], [0., 360., 0., 90.], radec=True)
        # ADM which targets are in pixels north of the Galactic plane.
        galn = pixn[pixnum]
    
        # ADM which targets are in the northern imaging area.
        arean = (targets["DEC"] >= split) & galn
    
        # ADM retain 'N' targets in 'N' area and 'S' in 'S' area.
        #keep = (photn & arean) | (~photn & ~arean)
        #return targets[keep]

        inorth = photn & arean
        newphotsys = np.array(['S'] * len(targets))
        newphotsys[inorth] = 'N'

        return newphotsys

    def select(self, redrockfiles, zmin=0.001, zmax=None, zwarnmax=None,
               targetids=None, firsttarget=0, ntargets=None,
               use_quasarnet=True, redrockfile_prefix='redrock-',
               specfile_prefix='coadd-', qnfile_prefix='qso_qn-'):
        """Select targets for fitting and gather the necessary spectroscopic metadata.

        Parameters
        ----------
        redrockfiles : str or array
            Full path to one or more input Redrock file(s).
        zmin : float
            Minimum redshift of observed targets to select. Defaults to
            0.001. Note that any value less than or equal to zero will raise an
            exception because a positive redshift is needed to compute the
            distance modulus when modeling the broadband photometry.
        zmax : float or `None`
            Maximum redshift of observed targets to select. `None` is equivalent
            to not having an upper redshift limit.
        zwarnmax : int or `None`
            Maximum Redrock zwarn value for selected targets. `None` is
            equivalent to not having any cut on zwarn.
        targetids : int or array or `None`
            Restrict the sample to the set of targetids in this list. If `None`,
            select all targets which satisfy the selection criteria.
        firsttarget : int
            Integer offset of the first object to consider in each file. Useful
            for debugging and testing. Defaults to 0.
        ntargets : int or `None`
            Number of objects to analyze in each file. Useful for debugging and
            testing. If `None`, select all targets which satisfy the selection
            criteria.
        use_quasarnet : `bool`
            Use QuasarNet to improve QSO redshifts, if the afterburner file is
            present. Defaults to `True`.
        redrockfile_prefix : str
            Prefix of the `redrockfiles`. Defaults to `redrock-`.
        specfile_prefix : str
            Prefix of the spectroscopic coadds corresponding to the input
            Redrock file(s). Defaults to `coadd-`.
        qnfile_prefix : str
            Prefix of the QuasarNet afterburner file. Defaults to `qso_qn-`.

        Attributes
        ----------
        coadd_type : str
            Type of coadded spectra (healpix, cumulative, pernight, or perexp).
        meta : list of :class:`astropy.table.Table`
            Array of tables (one per input `redrockfile`) with the metadata
            needed to fit the data and to write to the output file(s).
        redrockfiles : str array
            Input Redrock file names.
        specfiles : str array
            Spectroscopic file names corresponding to each each input Redrock file.
        specprod : str
            Spectroscopic production name for the input Redrock file.

        Notes
        -----
        We assume that `specprod` is the same for all input Redrock files,
        although we don't explicitly do this check. Specifically, we only read
        the header of the first file.

        """
        from astropy.table import vstack, hstack
        from desiutil.depend import getdep
        from desitarget.io import releasedict        
        from desitarget.targets import main_cmx_or_sv
        from desispec.io.photo import gather_tractorphot

        if zmin <= 0.0:
            errmsg = 'zmin should generally be >= 0; proceed with caution!'
            log.warning(errmsg)
        
        if zmax is None:
            zmax = 99.0

        if zwarnmax is None:
            zwarnmax = 99999
            
        if zmin >= zmax:
            errmsg = 'zmin must be <= zmax.'
            log.critical(errmsg)
            raise ValueError(errmsg)
        
        if redrockfiles is None:
            errmsg = 'At least one redrockfiles file is required.'
            log.critical(errmsg)
            raise ValueError(errmsg)

        if len(np.atleast_1d(redrockfiles)) == 0:
            errmsg = 'No redrockfiles found!'
            log.warning(errmsg)
            raise ValueError(errmsg)

        # Should we not sort...?
        #redrockfiles = np.array(set(np.atleast_1d(redrockfiles)))
        redrockfiles = np.array(sorted(set(np.atleast_1d(redrockfiles))))
        log.info('Reading and parsing {} unique redrockfile(s).'.format(len(redrockfiles)))

        alltiles = []
        self.redrockfiles, self.specfiles, self.meta = [], [], []
        
        for ired, redrockfile in enumerate(np.atleast_1d(redrockfiles)):
            if not os.path.isfile(redrockfile):
                log.warning('File {} not found!'.format(redrockfile))
                continue

            if not redrockfile_prefix in redrockfile:
                errmsg = 'Redrockfile {} missing standard prefix {}; please specify redrockfile_prefix argument.'
                log.critical(errmsg)
                raise ValueError(errmsg)
            
            specfile = redrockfile.replace(redrockfile_prefix, specfile_prefix)
            if not os.path.isfile(specfile):
                log.warning('File {} not found!'.format(specfile))
                continue
            
            # Can we use the quasarnet afterburner file to improve QSO redshifts?
            qnfile = redrockfile.replace(redrockfile_prefix, qnfile_prefix)
            if os.path.isfile(qnfile) and use_quasarnet:
                use_qn = True
            else:
                use_qn = False

            # Gather some coadd information from the header. Note: this code is
            # only compatible with Fuji & Guadalupe headers and later.
            hdr = fitsio.read_header(specfile, ext=0)

            specprod = getdep(hdr, 'SPECPROD')
            if hasattr(self, 'specprod'):
                if self.specprod != specprod:
                    errmsg = 'specprod must be the same for all input redrock files! {}!={}'.format(specprod, self.specprod)
                    log.critical(errmsg)
                    raise ValueError(errmsg)
            
            self.specprod = specprod
            
            if specprod == 'fuji': # EDR
                TARGETINGCOLS = TARGETINGBITS[specprod]
            else:
                TARGETINGCOLS = TARGETINGBITS['default']

            if 'SPGRP' in hdr:
                self.coadd_type = hdr['SPGRP']
            else:
                errmsg = 'SPGRP header card missing from spectral file {}'.format(specfile)
                log.warning(errmsg)
                self.coadd_type = 'custom'

            #log.info('specprod={}, coadd_type={}'.format(self.specprod, self.coadd_type))

            if self.coadd_type == 'healpix':
                survey = hdr['SURVEY']
                program = hdr['PROGRAM']
                healpix = np.int32(hdr['SPGRPVAL'])
                thrunight = None
                log.info('specprod={}, coadd_type={}, survey={}, program={}, healpix={}'.format(
                    self.specprod, self.coadd_type, survey, program, healpix))

                # I'm not sure we need these attributes but if we end up
                # using them then be sure to document them as attributes of
                # the class!
                #self.hpxnside = hdr['HPXNSIDE']
                #self.hpxnest = hdr['HPXNEST']
            elif self.coadd_type == 'custom':
                survey = 'custom'
                program = 'custom'
                healpix = np.int32(0)
                thrunight = None
                log.info('specprod={}, coadd_type={}, survey={}, program={}, healpix={}'.format(
                    self.specprod, self.coadd_type, survey, program, healpix))
            else:
                tileid = np.int32(hdr['TILEID'])
                petal = np.int16(hdr['PETAL'])
                night = np.int32(hdr['NIGHT']) # thrunight for coadd_type==cumulative
                if self.coadd_type == 'perexp':
                    expid = np.int32(hdr['EXPID'])
                    log.info('specprod={}, coadd_type={}, tileid={}, petal={}, night={}, expid={}'.format(
                        self.specprod, self.coadd_type, tileid, petal, night, expid))
                else:
                    expid = None
                    log.info('specprod={}, coadd_type={}, tileid={}, petal={}, night={}'.format(
                        self.specprod, self.coadd_type, tileid, petal, night))

                # cache the tiles file so we can grab the survey and program name appropriate for this tile
                if not hasattr(self, 'tileinfo'):
                    infofile = os.path.join(self.redux_dir, self.specprod, 'tiles-{}.csv'.format(self.specprod))
                    if os.path.isfile(infofile):
                        self.tileinfo = Table.read(infofile)
                        
                if hasattr(self, 'tileinfo'):
                    tileinfo = self.tileinfo[self.tileinfo['TILEID'] == tileid]
                    survey = tileinfo['SURVEY'][0]
                    program = tileinfo['PROGRAM'][0]
                    
            # add targeting columns
            allfmcols = np.array(fitsio.FITS(specfile)['FIBERMAP'].get_colnames())
            READFMCOLS = FMCOLS + [col for col in TARGETINGCOLS if col in allfmcols]
                    
            # If targetids is *not* given we have to choose "good" objects
            # before subselecting (e.g., we don't want sky spectra).
            if targetids is None:
                zb = fitsio.read(redrockfile, 'REDSHIFTS', columns=REDSHIFTCOLS)
                # Are we reading individual exposures or coadds?
                meta = fitsio.read(specfile, 'FIBERMAP', columns=READFMCOLS)
                assert(np.all(zb['TARGETID'] == meta['TARGETID']))
                # need to also update mpi.get_ntargets_one
                fitindx = np.where((zb['Z'] > zmin) * (zb['Z'] < zmax) *
                                   (meta['OBJTYPE'] == 'TGT') * (zb['ZWARN'] <= zwarnmax) *
                                   (zb['ZWARN'] & ZWarningMask.NODATA == 0))[0]
            else:
                # We already know we like the input targetids, so no selection
                # needed.
                alltargetids = fitsio.read(redrockfile, 'REDSHIFTS', columns='TARGETID')
                fitindx = np.where([tid in targetids for tid in alltargetids])[0]                
                
            if len(fitindx) == 0:
                log.info('No requested targets found in redrockfile {}'.format(redrockfile))
                continue

            # Do we want just a subset of the available objects?
            if ntargets is None:
                _ntargets = len(fitindx)
            else:
                _ntargets = ntargets
            if _ntargets > len(fitindx):
                log.warning('Number of requested ntargets exceeds the number of targets on {}; reading all of them.'.format(
                    redrockfile))

            __ntargets = len(fitindx)
            fitindx = fitindx[firsttarget:firsttarget+_ntargets]
            if len(fitindx) == 0:
                log.info('All {} targets in redrockfile {} have been dropped (firsttarget={}, ntargets={}).'.format(
                    __ntargets, redrockfile, firsttarget, _ntargets))
                continue
                
            # If firsttarget is a large index then the set can become empty.
            if targetids is None:
                zb = Table(zb[fitindx])
                meta = Table(meta[fitindx])
            else:
                zb = Table(fitsio.read(redrockfile, 'REDSHIFTS', rows=fitindx, columns=REDSHIFTCOLS))
                meta = Table(fitsio.read(specfile, 'FIBERMAP', rows=fitindx, columns=READFMCOLS))
            tsnr2 = Table(fitsio.read(redrockfile, 'TSNR2', rows=fitindx, columns=TSNR2COLS))
            assert(np.all(zb['TARGETID'] == meta['TARGETID']))

            # Update the redrock redshift when quasarnet disagrees **but only
            # for QSO targets**. From Edmond: the QN afterburner is run with a
            # threshold 0.5. With VI, we choose 0.95 as final threshold. Note,
            # the IS_QSO_QN_NEW_RR column contains only QSO for QN which are not
            # QSO for RR.
            zb['Z_RR'] = zb['Z'] # add it at the end
            if use_qn:
                surv_target, surv_mask, surv = main_cmx_or_sv(meta)
                if surv == 'cmx':
                    desi_target = surv_target[0]
                    desi_mask = surv_mask[0]
                    # need to check multiple QSO masks
                    IQSO = []
                    for bitname in desi_mask.names():
                        if 'QSO' in bitname:
                            IQSO.append(np.where(meta[desi_target] & desi_mask[bitname] != 0)[0])
                    if len(IQSO) > 0:
                        IQSO = np.sort(np.unique(np.hstack(IQSO)))
                else:
                    desi_target, bgs_target, mws_target = surv_target
                    desi_mask, bgs_mask, mws_mask = surv_mask
                    IQSO = np.where(meta[desi_target] & desi_mask['QSO'] != 0)[0]
                if len(IQSO) > 0:
                    qn = Table(fitsio.read(qnfile, 'QN_RR', rows=fitindx[IQSO], columns=QNCOLS))
                    assert(np.all(qn['TARGETID'] == meta['TARGETID'][IQSO]))
                    log.info('Updating QSO redshifts using a QN threshold of 0.95.')
                    qn['IS_QSO_QN'] = np.max(np.array([qn[name] for name in QNLINES]), axis=0) > 0.95
                    qn['IS_QSO_QN_NEW_RR'] &= qn['IS_QSO_QN']
                    if np.count_nonzero(qn['IS_QSO_QN_NEW_RR']) > 0:
                        zb['Z'][IQSO[qn['IS_QSO_QN_NEW_RR']]] = qn['Z_NEW'][qn['IS_QSO_QN_NEW_RR']]
                    del qn

            # astropy 5.0 "feature" -- join no longer preserves order, ugh.
            zb.remove_column('TARGETID')
            meta = hstack((zb, meta, tsnr2))
            #meta = join(zb, meta, keys='TARGETID')
            del zb, tsnr2

            # Get the unique set of tiles contributing to the coadded spectra
            # from EXP_FIBERMAP.
            expmeta = fitsio.read(specfile, 'EXP_FIBERMAP', columns=EXPFMCOLS[self.coadd_type])
            I = np.isin(expmeta['TARGETID'], meta['TARGETID'])
            if np.count_nonzero(I) == 0:
                errmsg = 'No matching targets in exposure table.'
                log.critical(errmsg)
                raise ValueError(errmsg)
            expmeta = Table(expmeta[I])

            #tiles = np.unique(np.atleast_1d(expmeta['TILEID']).data)
            #alltiles.append(tiles)

            # build the list of tiles that went into each unique target / coadd
            tileid_list = [] # variable length, so need to build the array first
            for tid in meta['TARGETID']:
                I = tid == expmeta['TARGETID']
                tileid_list.append(' '.join(np.unique(expmeta['TILEID'][I]).astype(str)))
                #meta['TILEID_LIST'][M] = ' '.join(np.unique(expmeta['TILEID'][I]).astype(str))
                if self.coadd_type == 'healpix':
                    alltiles.append(expmeta['TILEID'][I][0]) # store just the zeroth tile for gather_targetphot, below
                elif self.coadd_type == 'custom':
                    alltiles.append(expmeta['TILEID'][I][0]) # store just the zeroth tile for gather_targetphot, below
                else:
                    alltiles.append(tileid)
            if self.coadd_type == 'healpix':                    
                meta['TILEID_LIST'] = tileid_list
            elif self.coadd_type == 'custom':
                meta['TILEID_LIST'] = tileid_list

            # Gather additional info about this pixel.
            if self.coadd_type == 'healpix':
                meta['SURVEY'] = survey
                meta['PROGRAM'] = program
                meta['HEALPIX'] = healpix
            elif self.coadd_type == 'custom':
                meta['SURVEY'] = survey
                meta['PROGRAM'] = program
                meta['HEALPIX'] = healpix
            else:
                if hasattr(self, 'tileinfo'):
                    meta['SURVEY'] = survey
                    meta['PROGRAM'] = program
                meta['NIGHT'] = night
                meta['TILEID'] = tileid
                if expid:
                    meta['EXPID'] = expid

                # get the correct fiber number
                if 'FIBER' in expmeta.colnames:
                    meta['FIBER'] = np.zeros(len(meta), dtype=expmeta['FIBER'].dtype)
                    for iobj, tid in enumerate(meta['TARGETID']):
                        iexp = np.where(expmeta['TARGETID'] == tid)[0][0] # zeroth
                        meta['FIBER'][iobj] = expmeta['FIBER'][iexp]

            self.meta.append(Table(meta))
            self.redrockfiles.append(redrockfile)
            self.specfiles.append(specfile)

        if len(self.meta) == 0:
            log.warning('No targets read!')
            return

        # Use the metadata in the fibermap to retrieve the LS-DR9 source
        # photometry.
        t0 = time.time()
        targets = gather_tractorphot(vstack(self.meta), columns=TARGETCOLS, dr9dir=self.dr9dir)
        #targets = gather_tractorphot(vstack(self.meta), columns=np.hstack((
        #    TARGETCOLS, 'FRACFLUX_W1', 'FRACFLUX_W2', 'FRACFLUX_W3', 'FRACFLUX_W4')), dr9dir=self.dr9dir)

        # bug! https://github.com/desihub/fastspecfit/issues/75
        #from desitarget.io import releasedict
        #for imeta, meta in enumerate(self.meta):
        #    ibug = np.where((meta['RELEASE'] > 0) * (meta['BRICKID'] > 0) * (meta['BRICK_OBJID'] > 0) * (meta['PHOTSYS'] == ''))[0]
        #    if len(ibug) > 0:
        #        meta['PHOTSYS', 'RELEASE', 'BRICKID', 'BRICK_OBJID', 'TARGETID', 'TILEID', 'NIGHT', 'FIBER'][ibug]
        #        from desitarget.targets import decode_targetid
        #        objid, brickid, release, mock, sky, gaia = decode_targetid(meta['TARGETID'][ibug])
        #        meta['PHOTSYS'][ibug] = [releasedict[release] if release >= 9000 else '' for release in meta['RELEASE'][ibug]]
        #        self.meta[imeta] = meta                    
        #targets = gather_tractorphot(vstack(self.meta), columns=TARGETCOLS, dr9dir=self.dr9dir)

        metas = []
        for meta in self.meta:
            srt = np.hstack([np.where(tid == targets['TARGETID'])[0] for tid in meta['TARGETID']])
            assert(np.all(meta['TARGETID'] == targets['TARGETID'][srt]))
            # Prefer the target catalog quantities over those in the fiberassign
            # table, unless the target catalog is zero.
            for col in targets.colnames:
                meta[col] = targets[col][srt]
                
            # special case for some secondary and ToOs
            I = (meta['RA'] == 0) * (meta['DEC'] == 0) * (meta['TARGET_RA'] != 0) * (meta['TARGET_DEC'] != 0)
            if np.sum(I) > 0:
                meta['RA'][I] = meta['TARGET_RA'][I]
                meta['DEC'][I] = meta['TARGET_DEC'][I]
            assert(np.all((meta['RA'] != 0) * (meta['DEC'] != 0)))
                
            # try to repair PHOTSYS
            # https://github.com/desihub/fastspecfit/issues/75
            I = np.logical_and(meta['PHOTSYS'] != 'N', meta['PHOTSYS'] != 'S') * (meta['RELEASE'] >= 9000)
            if np.sum(I) > 0:
                meta['PHOTSYS'][I] = [releasedict[release] if release >= 9000 else '' for release in meta['RELEASE'][I]]
            I = np.logical_and(meta['PHOTSYS'] != 'N', meta['PHOTSYS'] != 'S')
            if np.sum(I) > 0:
                meta['PHOTSYS'][I] = self.resolve(meta[I])
            I = np.logical_and(meta['PHOTSYS'] != 'N', meta['PHOTSYS'] != 'S')
            if np.sum(I) > 0:
                errmsg = 'Unsupported value of PHOTSYS.'
                log.critical(errmsg)
                raise ValueError(errmsg)
            
            # placeholders (to be added in DESISpectra.read_and_unpack)
            meta['EBV'] = np.zeros(shape=(1,), dtype='f4')
            for band in ['G', 'R', 'Z', 'W1', 'W2', 'W3', 'W4']:
                meta['MW_TRANSMISSION_{}'.format(band)] = np.ones(shape=(1,), dtype='f4')
            metas.append(meta)
            
        log.info('Gathered photometric metadata for {} objects in {:.2f} sec'.format(len(targets), time.time()-t0))
        self.meta = metas # update

    def read_and_unpack(self, fastphot=False, synthphot=True, mp=1):
        """Read and unpack selected spectra or broadband photometry.
        
        Parameters
        ----------
        fastphot : bool
            Read and unpack the broadband photometry; otherwise, handle the DESI
            three-camera spectroscopy. Optional; defaults to `False`.
        synthphot : bool
            Synthesize photometry from the coadded optical spectrum. Optional;
            defaults to `True`.
        remember_coadd : bool
            Add the coadded spectrum to the returned dictionary. Optional;
            defaults to `False` (in order to reduce memory usage).

        Returns
        -------
        List of dictionaries (:class:`dict`, one per object) the following keys:
            targetid : numpy.int64
                DESI target ID.
            zredrock : numpy.float64
                Redrock redshift.
            cameras : :class:`list`
                List of camera names present for this spectrum.
            wave : :class:`list`
                Three-element list of `numpy.ndarray` wavelength vectors, one for
                each camera.    
            flux : :class:`list`
                Three-element list of `numpy.ndarray` flux spectra, one for each
                camera and corrected for Milky Way extinction.
            ivar : :class:`list`
                Three-element list of `numpy.ndarray` inverse variance spectra, one
                for each camera.    
            res : :class:`list`
                Three-element list of :class:`desispec.resolution.Resolution`
                objects, one for each camera.
            snr : `numpy.ndarray`
                Median per-pixel signal-to-noise ratio in the grz cameras.
            linemask : :class:`list`
                Three-element list of `numpy.ndarray` boolean emission-line masks,
                one for each camera. This mask is used during continuum-fitting.
            linename : :class:`list`
                Three-element list of emission line names which might be present
                in each of the three DESI cameras.
            linepix : :class:`list`
                Three-element list of pixel indices, one per camera, which were
                identified in :class:`FFit.build_linemask` to belong to emission
                lines.
            contpix : :class:`list`
                Three-element list of pixel indices, one per camera, which were
                identified in :class:`FFit.build_linemask` to not be
                "contaminated" by emission lines.
            coadd_wave : `numpy.ndarray`
                Coadded wavelength vector with all three cameras combined.
            coadd_flux : `numpy.ndarray`
                Flux corresponding to `coadd_wave`.
            coadd_ivar : `numpy.ndarray`
                Inverse variance corresponding to `coadd_flux`.
            photsys : str
                Photometric system.
            phot : `astropy.table.Table`
                Total photometry in `grzW1W2`, corrected for Milky Way extinction.
            fiberphot : `astropy.table.Table`
                Fiber photometry in `grzW1W2`, corrected for Milky Way extinction.
            fibertotphot : `astropy.table.Table`
                Fibertot photometry in `grzW1W2`, corrected for Milky Way extinction.
            synthphot : :class:`astropy.table.Table`
                Photometry in `grz` synthesized from the Galactic
                extinction-corrected coadded spectra (with a mild extrapolation
                of the data blueward and redward to accommodate the g-band and
                z-band filter curves, respectively.

        """
        from astropy.table import vstack
        from desispec.coaddition import coadd_cameras
        from desispec.io import read_spectra
        from desiutil.dust import SFDMap
        from fastspecfit.continuum import ContinuumTools
        
        CTools = ContinuumTools()
        SFD = SFDMap(scaling=1.0, mapdir=self.mapdir)

        alldata = []
        for ispec, (specfile, meta) in enumerate(zip(self.specfiles, self.meta)):
            nobj = len(meta)
            if nobj == 1:
                log.info('Reading {} spectrum from {}'.format(nobj, specfile))
            else:
                log.info('Reading {} spectra from {}'.format(nobj, specfile))

            ebv = SFD.ebv(meta['RA'], meta['DEC'])

            # Age, luminosity, and distance modulus.
            dlum = self.luminosity_distance(meta['Z'])
            dmod = self.distance_modulus(meta['Z'])
            tuniv = self.universe_age(meta['Z'])
            
            if fastphot:
                unpackargs = []
                for igal in np.arange(len(meta)):
                    specdata = {
                        'targetid': meta['TARGETID'][igal], 'zredrock': meta['Z'][igal],
                        'photsys': meta['PHOTSYS'][igal],
                        'dluminosity': dlum[igal], 'dmodulus': dmod[igal], 'tuniv': tuniv[igal],
                        }
                    unpackargs.append((igal, specdata, meta[igal], ebv[igal], CTools, True, False))
            else:
                from desispec.resolution import Resolution
                
                spec = read_spectra(specfile).select(targets=meta['TARGETID'])
                assert(np.all(spec.fibermap['TARGETID'] == meta['TARGETID']))

                # Coadd across cameras.
                t0 = time.time()                
                coadd_spec = coadd_cameras(spec)
                log.info('Coadding across cameras took {:.2f} seconds.'.format(time.time()-t0))

                # unpack the desispec.spectra.Spectra objects into simple arrays
                cameras = spec.bands
                coadd_cameras = coadd_spec.bands[0]
                unpackargs = []
                for igal in np.arange(len(meta)):
                    specdata = {
                        'targetid': meta['TARGETID'][igal], 'zredrock': meta['Z'][igal],
                        'photsys': meta['PHOTSYS'][igal], 'cameras': cameras,
                        'dluminosity': dlum[igal], 'dmodulus': dmod[igal], 'tuniv': tuniv[igal],                        
                        'wave': [spec.wave[cam] for cam in cameras],
                        'flux': [spec.flux[cam][igal, :] for cam in cameras],
                        'ivar': [spec.ivar[cam][igal, :] for cam in cameras],
                        # Also track the mask---see https://github.com/desihub/desispec/issues/1389 
                        'mask': [spec.mask[cam][igal, :] for cam in cameras],
                        'res': [Resolution(spec.resolution_data[cam][igal, :, :]) for cam in cameras],
                        'coadd_wave': coadd_spec.wave[coadd_cameras],
                        'coadd_flux': coadd_spec.flux[coadd_cameras][igal, :],
                        'coadd_ivar': coadd_spec.ivar[coadd_cameras][igal, :],
                        'coadd_res': Resolution(coadd_spec.resolution_data[coadd_cameras][igal, :]),
                        }
                    unpackargs.append((igal, specdata, meta[igal], ebv[igal], CTools, fastphot, synthphot))
                    
            if mp > 1:
                import multiprocessing
                with multiprocessing.Pool(mp) as P:
                    out = P.map(_unpack_one_spectrum, unpackargs)
            else:
                out = [unpack_one_spectrum(*_unpackargs) for _unpackargs in unpackargs]
                
            out = list(zip(*out))
            self.meta[ispec] = Table(np.hstack(out[1]))
            alldata.append(out[0])
            del out
    
        alldata = np.concatenate(alldata)
        self.meta = vstack(self.meta)
        self.ntargets = len(self.meta)

        return alldata

def init_fastspec_output(input_meta, specprod, templates=None, ncoeff=None,
                         data=None, log=None, fastphot=False):
    """Initialize the fastspecfit output data and metadata table.

    Parameters
    ----------
    tile : :class:`str`
        Tile number.
    night : :class:`str`
        Night on which `tile` was observed.
    redrock : :class:`astropy.table.Table`
        Redrock redshift table (row-aligned to `fibermap`).
    fibermap : :class:`astropy.table.Table`
        Fiber map (row-aligned to `redrock`).

    Returns
    -------


    Notes
    -----

    Must provide templates or ncoeff.

    """
    import astropy.units as u
    from astropy.table import hstack, Column
    from fastspecfit.emlines import read_emlines        
    from fastspecfit.continuum import Filters

    if log is None:
        from desiutil.log import get_logger
        log = get_logger()

    linetable = read_emlines()
    Filt = Filters(load_filters=False)

    nobj = len(input_meta)

    # get the number of templates
    if ncoeff is None:
        if not os.path.isfile(templates):
            errmsg = 'Templates file not found {}'.format(templates)
            log.critical(errmsg)
            raise IOError(errmsg)
        
        templatehdr = fitsio.read_header(templates, ext='METADATA')
        ncoeff = templatehdr['NAXIS2']

    # The information stored in the metadata table depends on which spectra
    # were fitted (exposures, nightly coadds, deep coadds).
    fluxcols = ['PHOTSYS', 'LS_ID',
                #'RELEASE',
                'FIBERFLUX_G', 'FIBERFLUX_R', 'FIBERFLUX_Z',
                'FIBERTOTFLUX_G', 'FIBERTOTFLUX_R', 'FIBERTOTFLUX_Z', 
                'FLUX_G', 'FLUX_R', 'FLUX_Z', 'FLUX_W1', 'FLUX_W2', 'FLUX_W3', 'FLUX_W4',
                'FLUX_IVAR_G', 'FLUX_IVAR_R', 'FLUX_IVAR_Z',
                'FLUX_IVAR_W1', 'FLUX_IVAR_W2', 'FLUX_IVAR_W3', 'FLUX_IVAR_W4',
                'EBV',
                'MW_TRANSMISSION_G', 'MW_TRANSMISSION_R', 'MW_TRANSMISSION_Z',
                'MW_TRANSMISSION_W1', 'MW_TRANSMISSION_W2', 'MW_TRANSMISSION_W3', 'MW_TRANSMISSION_W4']
        
    colunit = {'RA': u.deg, 'DEC': u.deg, 'EBV': u.mag,
               'FIBERFLUX_G': 'nanomaggies', 'FIBERFLUX_R': 'nanomaggies', 'FIBERFLUX_Z': 'nanomaggies',
               'FIBERTOTFLUX_G': 'nanomaggies', 'FIBERTOTFLUX_R': 'nanomaggies', 'FIBERTOTFLUX_Z': 'nanomaggies',
               'FLUX_G': 'nanomaggies', 'FLUX_R': 'nanomaggies', 'FLUX_Z': 'nanomaggies',
               'FLUX_W1': 'nanomaggies', 'FLUX_W2': 'nanomaggies', 'FLUX_W3': 'nanomaggies', 'FLUX_W4': 'nanomaggies', 
               'FLUX_IVAR_G': 'nanomaggies-2', 'FLUX_IVAR_R': 'nanomaggies-2',
               'FLUX_IVAR_Z': 'nanomaggies-2', 'FLUX_IVAR_W1': 'nanomaggies-2',
               'FLUX_IVAR_W2': 'nanomaggies-2', 'FLUX_IVAR_W3': 'nanomaggies-2',
               'FLUX_IVAR_W4': 'nanomaggies-2',
               }

    skipcols = ['OBJTYPE', 'TARGET_RA', 'TARGET_DEC', 'BRICKNAME', 'BRICKID', 'BRICK_OBJID', 'RELEASE'] + fluxcols
    redrockcols = ['Z', 'ZWARN', 'DELTACHI2', 'SPECTYPE', 'Z_RR', 'TSNR2_BGS',
                   'TSNR2_LRG', 'TSNR2_ELG', 'TSNR2_QSO', 'TSNR2_LYA']
    
    meta = Table()
    metacols = input_meta.colnames

    # All of this business is so we can get the columns in the order we want
    # (i.e., the order that matches the data model).
    for metacol in ['TARGETID', 'SURVEY', 'PROGRAM', 'HEALPIX', 'TILEID', 'NIGHT', 'FIBER',
                    'EXPID', 'TILEID_LIST', 'RA', 'DEC', 'COADD_FIBERSTATUS']:
        if metacol in metacols:
            meta[metacol] = input_meta[metacol]
            if metacol in colunit.keys():
                meta[metacol].unit = colunit[metacol]

    if specprod == 'fuji': # EDR
        TARGETINGCOLS = TARGETINGBITS[specprod]
    else:
        TARGETINGCOLS = TARGETINGBITS['default']

    for metacol in metacols:
        if metacol in skipcols or metacol in TARGETINGCOLS or metacol in meta.colnames or metacol in redrockcols:
            continue
        else:
            meta[metacol] = input_meta[metacol]
            if metacol in colunit.keys():
                meta[metacol].unit = colunit[metacol]

    for bitcol in TARGETINGCOLS:
        if bitcol in metacols:
            meta[bitcol] = input_meta[bitcol]
        else:
            meta[bitcol] = np.zeros(shape=(1,), dtype=np.int64)

    for redrockcol in redrockcols:
        if redrockcol in metacols: # the Z_RR from quasarnet may not be present
            meta[redrockcol] = input_meta[redrockcol]
        if redrockcol in colunit.keys():
            meta[redrockcol].unit = colunit[redrockcol]

    for fluxcol in fluxcols:
        meta[fluxcol] = input_meta[fluxcol]
        if fluxcol in colunit.keys():
            meta[fluxcol].unit = colunit[fluxcol]

    # fastspec table
    out = Table()
    for col in ['TARGETID', 'SURVEY', 'PROGRAM', 'HEALPIX', 'TILEID', 'NIGHT', 'FIBER', 'EXPID']:
        if col in metacols:
            out[col] = input_meta[col]

    out.add_column(Column(name='Z', length=nobj, dtype='f8')) # redshift
    out.add_column(Column(name='COEFF', length=nobj, shape=(ncoeff,), dtype='f4'))

    out.add_column(Column(name='RCHI2', length=nobj, dtype='f4'))      # full-spectrum reduced chi2
    out.add_column(Column(name='RCHI2_CONT', length=nobj, dtype='f4')) # rchi2 fitting just to the continuum (spec+phot)
    out.add_column(Column(name='RCHI2_PHOT', length=nobj, dtype='f4')) # rchi2 fitting just to the photometry (=RCHI2_CONT if fastphot=True)

    if not fastphot:
        for cam in ['B', 'R', 'Z']:
            out.add_column(Column(name='SNR_{}'.format(cam), length=nobj, dtype='f4')) # median S/N in each camera
        for cam in ['B', 'R', 'Z']:
            out.add_column(Column(name='SMOOTHCORR_{}'.format(cam), length=nobj, dtype='f4')) 

    out.add_column(Column(name='VDISP', length=nobj, dtype='f4', unit=u.kilometer/u.second))
    out.add_column(Column(name='VDISP_IVAR', length=nobj, dtype='f4', unit=u.second**2/u.kilometer**2))
    out.add_column(Column(name='AV', length=nobj, dtype='f4', unit=u.mag))
    out.add_column(Column(name='AGE', length=nobj, dtype='f4', unit=u.Gyr))
    out.add_column(Column(name='ZZSUN', length=nobj, dtype='f4'))
    out.add_column(Column(name='LOGMSTAR', length=nobj, dtype='f4', unit=u.solMass))
    out.add_column(Column(name='SFR', length=nobj, dtype='f4', unit=u.solMass/u.year))
    #out.add_column(Column(name='FAGN', length=nobj, dtype='f4'))
    
    if not fastphot:
        out.add_column(Column(name='DN4000', length=nobj, dtype='f4'))
        out.add_column(Column(name='DN4000_OBS', length=nobj, dtype='f4'))
        out.add_column(Column(name='DN4000_IVAR', length=nobj, dtype='f4'))
    out.add_column(Column(name='DN4000_MODEL', length=nobj, dtype='f4'))

    # observed-frame photometry synthesized from the spectra
    for band in Filt.synth_bands:
        out.add_column(Column(name='FLUX_SYNTH_{}'.format(band.upper()), length=nobj, dtype='f4', unit='nanomaggies')) 
        #out.add_column(Column(name='FLUX_SYNTH_IVAR_{}'.format(band.upper()), length=nobj, dtype='f4', unit='nanomaggies-2'))
    # observed-frame photometry synthesized the best-fitting spectroscopic model
    for band in Filt.synth_bands:
        out.add_column(Column(name='FLUX_SYNTH_SPECMODEL_{}'.format(band.upper()), length=nobj, dtype='f4', unit='nanomaggies'))
    # observed-frame photometry synthesized the best-fitting continuum model
    for band in Filt.bands:
        out.add_column(Column(name='FLUX_SYNTH_PHOTMODEL_{}'.format(band.upper()), length=nobj, dtype='f4', unit='nanomaggies'))

    for band in Filt.absmag_bands:
        out.add_column(Column(name='KCORR_{}'.format(band.upper()), length=nobj, dtype='f4', unit=u.mag))
        out.add_column(Column(name='ABSMAG_{}'.format(band.upper()), length=nobj, dtype='f4', unit=u.mag)) # absolute magnitudes
        out.add_column(Column(name='ABSMAG_IVAR_{}'.format(band.upper()), length=nobj, dtype='f4', unit=1/u.mag**2))

    for cflux in ['LOGLNU_1500', 'LOGLNU_2800']:
        out.add_column(Column(name=cflux, length=nobj, dtype='f4', unit=10**(-28)*u.erg/u.second/u.Hz))
    out.add_column(Column(name='LOGL_5100', length=nobj, dtype='f4', unit=10**(10)*u.solLum))

    for cflux in ['FOII_3727_CONT', 'FHBETA_CONT', 'FOIII_5007_CONT', 'FHALPHA_CONT']:
        out.add_column(Column(name=cflux, length=nobj, dtype='f4', unit=10**(-17)*u.erg/(u.second*u.cm**2*u.Angstrom)))

    if not fastphot:
        # Add chi2 metrics
        #out.add_column(Column(name='DOF', length=nobj, dtype='i8')) # full-spectrum dof
        out.add_column(Column(name='RCHI2_LINE', length=nobj, dtype='f4')) # reduced chi2 with broad line-emission
        #out.add_column(Column(name='DOF_BROAD', length=nobj, dtype='i8'))
        out.add_column(Column(name='DELTA_LINERCHI2', length=nobj, dtype='f4')) # delta-reduced chi2 with and without broad line-emission

        # aperture corrections
        out.add_column(Column(name='APERCORR', length=nobj, dtype='f4')) # median aperture correction
        out.add_column(Column(name='APERCORR_G', length=nobj, dtype='f4'))
        out.add_column(Column(name='APERCORR_R', length=nobj, dtype='f4'))
        out.add_column(Column(name='APERCORR_Z', length=nobj, dtype='f4'))

        out.add_column(Column(name='NARROW_Z', length=nobj, dtype='f8'))
        out.add_column(Column(name='NARROW_ZRMS', length=nobj, dtype='f8'))
        out.add_column(Column(name='BROAD_Z', length=nobj, dtype='f8'))
        out.add_column(Column(name='BROAD_ZRMS', length=nobj, dtype='f8'))
        out.add_column(Column(name='UV_Z', length=nobj, dtype='f8'))
        out.add_column(Column(name='UV_ZRMS', length=nobj, dtype='f8'))

        out.add_column(Column(name='NARROW_SIGMA', length=nobj, dtype='f4', unit=u.kilometer / u.second))
        out.add_column(Column(name='NARROW_SIGMARMS', length=nobj, dtype='f4', unit=u.kilometer / u.second))
        out.add_column(Column(name='BROAD_SIGMA', length=nobj, dtype='f4', unit=u.kilometer / u.second))
        out.add_column(Column(name='BROAD_SIGMARMS', length=nobj, dtype='f4', unit=u.kilometer / u.second))
        out.add_column(Column(name='UV_SIGMA', length=nobj, dtype='f4', unit=u.kilometer / u.second))
        out.add_column(Column(name='UV_SIGMARMS', length=nobj, dtype='f4', unit=u.kilometer / u.second))

        # special columns for the fitted doublets
        out.add_column(Column(name='MGII_DOUBLET_RATIO', length=nobj, dtype='f4'))
        out.add_column(Column(name='OII_DOUBLET_RATIO', length=nobj, dtype='f4'))
        out.add_column(Column(name='SII_DOUBLET_RATIO', length=nobj, dtype='f4'))

        for line in linetable['name']:
            line = line.upper()
            out.add_column(Column(name='{}_AMP'.format(line), length=nobj, dtype='f4',
                                  unit=10**(-17)*u.erg/(u.second*u.cm**2*u.Angstrom)))
            out.add_column(Column(name='{}_AMP_IVAR'.format(line), length=nobj, dtype='f4',
                                  unit=10**34*u.second**2*u.cm**4*u.Angstrom**2/u.erg**2))
            out.add_column(Column(name='{}_FLUX'.format(line), length=nobj, dtype='f4',
                                  unit=10**(-17)*u.erg/(u.second*u.cm**2)))
            out.add_column(Column(name='{}_FLUX_IVAR'.format(line), length=nobj, dtype='f4',
                                  unit=10**34*u.second**2*u.cm**4/u.erg**2))
            out.add_column(Column(name='{}_BOXFLUX'.format(line), length=nobj, dtype='f4',
                                  unit=10**(-17)*u.erg/(u.second*u.cm**2)))
            out.add_column(Column(name='{}_BOXFLUX_IVAR'.format(line), length=nobj, dtype='f4',
                                  unit=10**34*u.second**2*u.cm**4/u.erg**2))
            
            out.add_column(Column(name='{}_VSHIFT'.format(line), length=nobj, dtype='f4',
                                  unit=u.kilometer/u.second))
            out.add_column(Column(name='{}_SIGMA'.format(line), length=nobj, dtype='f4',
                                  unit=u.kilometer / u.second))
            
            out.add_column(Column(name='{}_CONT'.format(line), length=nobj, dtype='f4',
                                  unit=10**(-17)*u.erg/(u.second*u.cm**2*u.Angstrom)))
            out.add_column(Column(name='{}_CONT_IVAR'.format(line), length=nobj, dtype='f4',
                                  unit=10**34*u.second**2*u.cm**4*u.Angstrom**2/u.erg**2))
            out.add_column(Column(name='{}_EW'.format(line), length=nobj, dtype='f4',
                                  unit=u.Angstrom))
            out.add_column(Column(name='{}_EW_IVAR'.format(line), length=nobj, dtype='f4',
                                  unit=1/u.Angstrom**2))
            out.add_column(Column(name='{}_FLUX_LIMIT'.format(line), length=nobj, dtype='f4',
                                  unit=u.erg/(u.second*u.cm**2)))
            out.add_column(Column(name='{}_EW_LIMIT'.format(line), length=nobj, dtype='f4',
                                  unit=u.Angstrom))
            out.add_column(Column(name='{}_CHI2'.format(line), length=nobj, dtype='f4'))
            out.add_column(Column(name='{}_NPIX'.format(line), length=nobj, dtype=np.int32))

    # Optionally copy over some quantities of interest from the data
    # dictionary. (This step is not needed when assigning units to the
    # output tables.)
    if data is not None:
        for iobj, _data in enumerate(data):
            out['Z'][iobj] = _data['zredrock']
            if not fastphot:
                for icam, cam in enumerate(_data['cameras']):
                    out['SNR_{}'.format(cam.upper())][iobj] = _data['snr'][icam]
            for iband, band in enumerate(Filt.fiber_bands):
                meta['FIBERTOTFLUX_{}'.format(band.upper())][iobj] = _data['fiberphot']['nanomaggies'][iband]
                #result['FIBERTOTFLUX_IVAR_{}'.format(band.upper())] = data['fiberphot']['nanomaggies_ivar'][iband]
            for iband, band in enumerate(Filt.bands):
                meta['FLUX_{}'.format(band.upper())][iobj] = _data['phot']['nanomaggies'][iband]
                meta['FLUX_IVAR_{}'.format(band.upper())][iobj] = _data['phot']['nanomaggies_ivar'][iband]

    return out, meta

def read_fastspecfit(fastfitfile, rows=None, columns=None, read_models=False):
    """Read the fitting results.

    """
    if os.path.isfile(fastfitfile):
        if 'FASTSPEC' in fitsio.FITS(fastfitfile):
            fastphot = False
            ext = 'FASTSPEC'
        else:
            fastphot = True
            ext = 'FASTPHOT'
            
        fastfit = Table(fitsio.read(fastfitfile, ext=ext, rows=rows, columns=columns))
        meta = Table(fitsio.read(fastfitfile, ext='METADATA', rows=rows, columns=columns))
        if read_models and ext == 'FASTSPEC':
            models = fitsio.read(fastfitfile, ext='MODELS')
            if rows is not None:
                models = models[rows, :, :]
        else:
            models = None
        log.info('Read {} object(s) from {}'.format(len(fastfit), fastfitfile))

        # Add specprod to the metadata table so that we can stack across
        # productions (e.g., Fuji+Guadalupe).
        hdr = fitsio.read_header(fastfitfile, ext='PRIMARY')
        if 'SPECPROD' in hdr:
            specprod = hdr['SPECPROD']
            meta['SPECPROD'] = specprod
            
        if 'COADDTYP' in hdr:
            coadd_type = hdr['COADDTYP']
        else:
            coadd_type = None

        if read_models:
            return fastfit, meta, coadd_type, fastphot, models
        else:
            return fastfit, meta, coadd_type, fastphot
    
    else:
        log.warning('File {} not found.'.format(fastfitfile))
        if read_models:
            return [None]*5
        else:
            return [None]*4

def write_fastspecfit(out, meta, modelspectra=None, outfile=None, specprod=None,
                      coadd_type=None, fastphot=False):
    """Write out.

    """
    import gzip, shutil
    from astropy.io import fits
    from desispec.io.util import fitsheader
    from desiutil.depend import add_dependencies, possible_dependencies

    t0 = time.time()
    outdir = os.path.dirname(os.path.abspath(outfile))
    if not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)

    nobj = len(out)
    if nobj == 1:
        log.info('Writing results for {} object to {}'.format(nobj, outfile))
    else:
        log.info('Writing results for {:,d} objects to {}'.format(nobj, outfile))
    
    if outfile.endswith('.gz'):
        tmpfile = outfile[:-3]+'.tmp'
    else:
        tmpfile = outfile+'.tmp'

    if fastphot:
        extname = 'FASTPHOT'
    else:
        extname = 'FASTSPEC'

    out.meta['EXTNAME'] = extname
    meta.meta['EXTNAME'] = 'METADATA'

    primhdr = []
    if specprod:
        primhdr.append(('EXTNAME', 'PRIMARY'))
        primhdr.append(('SPECPROD', (specprod, 'spectroscopic production name')))
    if coadd_type:
        primhdr.append(('COADDTYP', (coadd_type, 'spectral coadd type')))

    primhdr = fitsheader(primhdr)
    add_dependencies(primhdr, module_names=possible_dependencies+['fastspecfit'],
                     envvar_names=['DESI_ROOT', 'FTEMPLATES_DIR', 'DUST_DIR', 'DR9_DIR'])

    hdus = fits.HDUList()
    hdus.append(fits.PrimaryHDU(None, primhdr))
    hdus.append(fits.convenience.table_to_hdu(out))
    hdus.append(fits.convenience.table_to_hdu(meta))

    if modelspectra is not None:
        hdu = fits.ImageHDU(name='MODELS')
        # [nobj, 3, nwave]
        hdu.data = np.swapaxes(np.array([modelspectra['CONTINUUM'].data,
                                         modelspectra['SMOOTHCONTINUUM'].data,
                                         modelspectra['EMLINEMODEL'].data]), 0, 1)
        for key in modelspectra.meta.keys():
            hdu.header[key] = (modelspectra.meta[key][0], modelspectra.meta[key][1]) # all the spectra are identical, right??
                
        hdus.append(hdu)
        
    hdus.writeto(tmpfile, overwrite=True, checksum=True)

    # compress if needed (via another tempfile), otherwise just rename
    if outfile.endswith('.gz'):
        tmpfilegz = outfile[:-3]+'.tmp.gz'
        with open(tmpfile, 'rb') as f_in:
            with gzip.open(tmpfilegz, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.rename(tmpfilegz, outfile)
        os.remove(tmpfile)
    else:
        os.rename(tmpfile, outfile)

    log.info('Writing out took {:.2f} seconds.'.format(time.time()-t0))

def select(fastfit, metadata, coadd_type, healpixels=None, tiles=None,
           nights=None, return_index=False):
    """Optionally trim to a particular healpix or tile and/or night."""
    keep = np.ones(len(fastfit), bool)
    if coadd_type == 'healpix':
        if healpixels:
            pixelkeep = np.zeros(len(fastfit), bool)
            for healpixel in healpixels:
                pixelkeep = np.logical_or(pixelkeep, metadata['HEALPIX'].astype(str) == healpixel)
            keep = np.logical_and(keep, pixelkeep)
            log.info('Keeping {:,d} objects from healpixels(s) {}'.format(len(fastfit), ','.join(healpixels)))
    else:
        if tiles:
            tilekeep = np.zeros(len(fastfit), bool)
            for tile in tiles:
                tilekeep = np.logical_or(tilekeep, metadata['TILEID'].astype(str) == tile)
            keep = np.logical_and(keep, tilekeep)
            log.info('Keeping {:,d} objects from tile(s) {}'.format(len(fastfit), ','.join(tiles)))
        if nights and 'NIGHT' in metadata:
            nightkeep = np.zeros(len(fastfit), bool)
            for night in nights:
                nightkeep = np.logical_or(nightkeep, metadata['NIGHT'].astype(str) == night)
            keep = np.logical_and(keep, nightkeep)
            log.info('Keeping {:,d} objects from night(s) {}'.format(len(fastfit), ','.join(nights)))
            
    if return_index:
        return np.where(keep)[0]
    else:
        return fastfit[keep], metadata[keep]

def get_templates_filename(templateversion='1.0.0', imf='chabrier'):
    """Get the templates filename. """
    from fastspecfit.io import FTEMPLATES_DIR_NERSC
    templates_dir = os.environ.get('FTEMPLATES_DIR', FTEMPLATES_DIR_NERSC)
    templates = os.path.join(templates_dir, templateversion, 'ftemplates-{}-{}.fits'.format(
        imf, templateversion))
    return templates

def cache_templates(templates=None, templateversion='1.0.0', imf='chabrier',
                    mintemplatewave=None, maxtemplatewave=40e4, vdisp_nominal=125.0,
                    fastphot=False, log=None):
    """"Read the templates into a dictionary.

    """
    import fitsio
    from fastspecfit.continuum import _convolve_vdisp
    
    if log is None:
        from desiutil.log import get_logger
        log = get_logger()

    if templates is None:
        templates = get_templates_filename(templateversion='1.0.0', imf='chabrier')
        
    if not os.path.isfile(templates):
        errmsg = 'Templates file not found {}'.format(templates)
        log.critical(errmsg)
        raise IOError(errmsg)

    log.info('Reading {}'.format(templates))
    wave, wavehdr = fitsio.read(templates, ext='WAVE', header=True) # [npix]
    templateflux = fitsio.read(templates, ext='FLUX')  # [npix,nsed]
    templatelineflux = fitsio.read(templates, ext='LINEFLUX')  # [npix,nsed]
    templateinfo, templatehdr = fitsio.read(templates, ext='METADATA', header=True)
    
    continuum_pixkms = wavehdr['PIXSZBLU'] # pixel size [km/s]
    pixkms_wavesplit = wavehdr['PIXSZSPT'] # wavelength where the pixel size changes [A]

    # Trim the wavelengths and select the number/ages of the templates.
    # https://www.sdss.org/dr14/spectro/galaxy_mpajhu
    if mintemplatewave is None:
        mintemplatewave = np.min(wave)
    wavekeep = np.where((wave >= mintemplatewave) * (wave <= maxtemplatewave))[0]

    templatewave = wave[wavekeep]
    templateflux = templateflux[wavekeep, :]
    templateflux_nolines = templateflux - templatelineflux[wavekeep, :]
    del wave, templatelineflux
    
    # Cache a copy of the line-free templates at the nominal velocity
    # dispersion (needed for fastphot as well).
    I = np.where(templatewave < pixkms_wavesplit)[0]
    templateflux_nolines_nomvdisp = templateflux_nolines.copy()
    templateflux_nolines_nomvdisp[I, :] = _convolve_vdisp(templateflux_nolines_nomvdisp[I, :], vdisp_nominal,
                                                          pixsize_kms=continuum_pixkms)

    templateflux_nomvdisp = templateflux.copy()
    templateflux_nomvdisp[I, :] = _convolve_vdisp(templateflux_nomvdisp[I, :], vdisp_nominal,
                                                  pixsize_kms=continuum_pixkms)

    # pack into a dictionary
    templatecache = {'imf': templatehdr['IMF'],
                     #'nsed': len(templateinfo), 'npix': len(wavekeep),
                     'continuum_pixkms': continuum_pixkms,                     
                     'pixkms_wavesplit': pixkms_wavesplit,
                     'vdisp_nominal': vdisp_nominal,
                     'templateinfo': Table(templateinfo),
                     'templatewave': templatewave,
                     'templateflux': templateflux,
                     'templateflux_nomvdisp': templateflux_nomvdisp,
                     'templateflux_nolines': templateflux_nolines,
                     'templateflux_nolines_nomvdisp': templateflux_nolines_nomvdisp,
                     }
        
    if not fastphot:
        vdispwave = fitsio.read(templates, ext='VDISPWAVE')
        vdispflux, vdisphdr = fitsio.read(templates, ext='VDISPFLUX', header=True) # [nvdisppix,nvdispsed,nvdisp]

        # see bin/build-fsps-templates
        nvdisp = int(np.ceil((vdisphdr['VDISPMAX'] - vdisphdr['VDISPMIN']) / vdisphdr['VDISPRES'])) + 1
        vdisp = np.linspace(vdisphdr['VDISPMIN'], vdisphdr['VDISPMAX'], nvdisp)
    
        if not vdisp_nominal in vdisp:
            errmsg = 'Nominal velocity dispersion is not in velocity dispersion vector.'
            log.critical(errmsg)
            raise ValueError(errmsg)
    
        templatecache.update({
            'vdispflux': vdispflux,
            'vdispwave': vdispwave,
            'vdisp': vdisp,
            'vdisp_nominal_indx': np.where(vdisp == vdisp_nominal)[0],
            })

    return templatecache
