import numpy as np

def get_spectra(array_flt, array_trace, npix=5):
    '''
    Extract spectra by dividing the flat field and averaging the central
    two pixels

    Parameters
    ----------
    array_flt : 2d numpy array
        twilight image
    array_trace : 2d numpy array
        trace for each fiber
    wave : 2d numpy array
        wavelength for each fiber
    def_wave : 1d numpy array [GLOBAL]
        rectified wavelength

    Returns
    -------
    twi_spectrum : 2d numpy array
        rectified twilight spectrum for each fiber
    '''
    spec = np.zeros((array_trace.shape[0], array_trace.shape[1]))
    N = array_flt.shape[0]
    x = np.arange(array_flt.shape[1])
    LB = int((npix + 1) / 2)
    HB = -LB + npix + 1
    for fiber in np.arange(array_trace.shape[0]):
        if np.round(array_trace[fiber]).min() < LB:
            continue
        if np.round(array_trace[fiber]).max() >= (N - LB):
            continue
        indv = np.round(array_trace[fiber]).astype(int)
        for j in np.arange(-LB, HB):
            if j == -LB:
                w = indv + j + 1 - (array_trace[fiber] - npix / 2.)
            elif j == HB - 1:
                w = (npix / 2. + array_trace[fiber]) - (indv + j)
            else:
                w = 1.
            spec[fiber] += array_flt[indv + j, x] * w
    return spec / npix