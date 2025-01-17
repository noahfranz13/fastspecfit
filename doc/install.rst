.. _install:

Installation & Setup
====================

``FastSpecFit`` is simple to install and has a relatively small number of
standard dependencies. Here, we describe three different ways of setting up
``FastSpecFit``:

  1. :ref:`at NERSC (for DESI collaborators)<nersc installation>`;
  2. :ref:`on a laptop<laptop installation>`; or
  3. :ref:`using the Docker container<docker installation>`.

.. _nersc installation:

1. NERSC Installation
---------------------

At `NERSC`_, ``FastSpecFit`` can be loaded trivially on top of the standard DESI
software stack. In a login or interactive node simply run the following commands
and you are ready to go::

  source /global/cfs/cdirs/desi/software/desi_environment.sh main
  module load fastspecfit/main

Note that these commands will load the development (latest) versions of all the
software, which are not guaranteed to be stable. To load a specific set of
versions you can do::

  source /global/cfs/cdirs/desi/software/desi_environment.sh 22.5
  module load fastspecfit/v1.0.1

Alternatively, some users may want to access ``FastSpecFit`` through `NERSC`_'s
`JupyterHub`_ notebook server. To set up the kernel first do::

  mkdir -p ${HOME}/.local/share/jupyter/kernels/fastspecfit
  wget -O ${HOME}/.local/share/jupyter/kernels/fastspecfit/kernel.json \
    https://raw.githubusercontent.com/desihub/fastspecfit/main/etc/jupyter-kernel.json

Then, in `JupyterHub`_, simply select the *FastSpecFit* kernel and you are all
set!

.. _laptop installation:

2. Laptop Installation
----------------------

To install ``FastSpecFit`` and all its dependencies on a laptop we recommend a
dedicated `conda`_ environment. For example, to install everything transparently
into an environment called, e.g., *fastconda* one would do::

  conda create -y --name fastspecfit python numpy scipy numba astropy matplotlib seaborn
  conda activate fastconda
  pip install fitsio healpy speclite
  
  for package in desiutil desimodel desitarget desispec fastspecfit; do
    python -m pip install git+https://github.com/desihub/$package.git@main#egg=$package
  done

Alternatively, some users may want the DESI software to be installed in a more
accessible location (e.g., */path/to/desi/code*), in which case one would do::
  
  conda create -y --name fastconda python numpy scipy numba astropy matplotlib seaborn
  conda activate fastconda
  pip install fitsio healpy speclite

  export DESI_CODE=/path/to/desi/code
  mkdir -p $DESI_CODE
  
  pushd $DESI_CODE 
  for package in desiutil desimodel desitarget desispec fastspecfit; do
    git clone https://github.com/desihub/$package.git
    export PATH=$DESI_CODE/$package/bin:$PATH
    export PYTHONPATH=$DESI_CODE/$package/py:$PYTHONPATH
  done
  popd

Finally, ``FastSpecFit`` has three more data dependencies, each specified with
their own environment variable:

  * ``DESI_ROOT``, which specifies the top-level location of the DESI data;
  * ``DUST_DIR``, which specifies the location of the `Schlegel, Finkbeiner, &
    Davis dust maps`_; and
  * ``FASTSPECFIT_TEMPLATES``, which indicates the location of the simple
    stellar population (SSP) templates used to model the stellar continuum.

.. note::
   
  Currently, the DESI data are only available to DESI collaborators; however,
  the `Early Data Release (EDR)`_ is expected to be publicly available in early
  2023 and other data releases will be announced in the `DESI Data Release`_
  page, after which point the instructions here will be updated.

With the preceding caveat in mind, one can set up the remaining dependencies
with the following commands::

  export DESI_ROOT=/path/to/desi/data
  export DUST_DIR=/path/to/dustmaps
  export FASTSPECFIT_TEMPLATES=/path/to/templates/SSP-CKC14z

  wget -r -np -nH --cut-dirs 5 -A fits -P $DUST_DIR \
    "https://portal.nersc.gov/project/cosmo/data/dust/v0_1/maps"
  wget -r -e robots=off -np -nH --cut-dirs 4 -A fits -P $FASTSPECFIT_TEMPLATES \
    "https://data.desi.lbl.gov/public/external/templates/SSP-CKC14z/v1.0"
  
.. _docker installation:

3. Using Docker
---------------

Finally, for production runs and for expert users, ``FastSpecFit`` is also
available as a Docker container which is served publicly in the
`DockerHub/desihub`_ repository.

For example, on a laptop one would retrieve (or update) and enter the *v1.0.1*
version of the container with::
  
  docker pull desihub/fastspecfit:v1.0.1
  docker run -it desihub/fastspecfit:v1.0.1

Alternatively, at `NERSC`_ one would need to use `shifter`_::

  shifterimg pull docker:desihub/fastspecfit:v1.0.1
  shifter --image docker:desihub/fastspecfit:v1.0.1 bash

However, neither of the preceding commands define the required environment
variables, although we provide a simple setup script which does. For simple
interactive work at `NERSC`_ (e.g., in a login node) do::

  mkdir -p /path/to/fastspecfit/setup/script
  wget https://raw.githubusercontent.com/desihub/fastspecfit/main/bin/fastspecfit-setup.sh \
    -O /path/to/fastspecfit/setup/script/fastspecfit-setup.sh

  /path/to/fastspecfit/setup/script/fastspecfit-setup.sh shifter
  source /path/to/fastspecfit/setup/script/fastspecfit-setup.sh env

.. note::
  To run ``FastSpecFit`` on a large sample of objects (or for a full production
  or data release), please do not use a login node; instead, see the
  :ref:`running_fastspecfit` documentation for instructions and best practices.

.. _`conda`: https://anaconda.org/

.. _`Schlegel, Finkbeiner, & Davis dust maps`: https://ui.adsabs.harvard.edu/abs/1998ApJ...500..525S/abstract

.. _`NERSC`: https://www.nersc.gov/

.. _`JupyterHub`: https://jupyter.nersc.gov/ 

.. _`DockerHub/desihub`: https://hub.docker.com/u/desihub

.. _`shifter`: https://docs.nersc.gov/development/shifter/

.. _`Early Data Release (EDR)`: https://data.desi.lbl.gov/doc/releases/edr/

.. _`Data Release 1 (DR1)`: https://data.desi.lbl.gov/doc/releases/dr1

.. _`DESI Data Release`: https://data.desi.lbl.gov
