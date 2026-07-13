from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, List
import numpy as np
import matplotlib.pyplot as plt


def plot_identify_arc_summary(
    out_folder: Path,
    ref_profile: Optional[np.ndarray],
    best: Optional[Dict],
    filename: str = "identify_arc_summary.png",
) -> Optional[Path]:
    """Diagnostic for arc identification using algorithms.wave._identify_arc outputs.

    Panels:
    - Top: spectrum with candidate peaks (blue dashed), matched peaks (green), and unmatched candidates (red x).
    - Bottom: residuals (wave_fit - wave_ref) vs wave_ref for matched peaks, annotated with RMS and nmatch.
    """
    if ref_profile is None or best is None:
        return None

    prof = np.asarray(ref_profile, dtype=float).ravel()
    n = int(prof.size)
    if n == 0:
        return None
    x = np.arange(n)

    guesses = np.asarray((best or {}).get("peak_x_all", []), dtype=float).ravel()
    det = np.asarray((best or {}).get("detected_x", []), dtype=float).ravel()

    tol_pix = 2.5
    if guesses.size == 0:
        guesses = np.full(0, np.nan)
    if det.size == 0:
        det = np.full(0, np.nan)
    if guesses.size > 0 and det.size > 0 and np.isfinite(det).any():
        gfin = np.isfinite(guesses)
        dfin = np.isfinite(det)
        if np.any(gfin) and np.any(dfin):
            dmin = np.full(guesses.shape, np.inf, dtype=float)
            dmin[gfin] = np.min(np.abs(guesses[gfin, None] - det[dfin][None, :]), axis=1)
            matched_mask = dmin <= tol_pix
        else:
            matched_mask = np.zeros(guesses.shape, dtype=bool)
    else:
        matched_mask = np.zeros(guesses.shape, dtype=bool)
    missing = (~matched_mask) & np.isfinite(guesses)

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(11, 6.5), gridspec_kw=dict(height_ratios=[2, 1]))

    finite_prof = prof[np.isfinite(prof)]
    if finite_prof.size:
        lo, hi = np.percentile(finite_prof, [1, 99])
        if hi <= lo:
            hi = lo + 1e-6
    else:
        lo, hi = 0.0, 1.0
    ax_top.plot(x, prof, color='0.2', lw=0.8)
    if np.isfinite(guesses).any():
        ax_top.vlines(guesses[np.isfinite(guesses)], lo, hi, colors='tab:blue', linestyles='--', alpha=0.5, lw=1.0, label='candidates')
    if np.isfinite(det).any():
        ax_top.vlines(det[np.isfinite(det)], lo, hi, colors='tab:green', linestyles='-', alpha=0.7, lw=1.2, label='matched')
    if np.any(missing):
        gx = guesses[missing]
        ax_top.plot(gx, np.full_like(gx, hi), 'x', color='tab:red', ms=6, mew=1.2, label='missing')
    ax_top.set_xlim(0, n - 1)
    ax_top.set_ylim(lo, hi)
    ax_top.set_ylabel('Ref profile (arb)')
    ax_top.legend(ncol=3, frameon=False, loc='upper right')
    try:
        info = f"nmatch={int((best or {}).get('nmatch', 0))}, rms={float((best or {}).get('rms', np.nan)):.3f}"
    except Exception:
        info = ""
    if info:
        ax_top.text(0.01, 0.95, info, transform=ax_top.transAxes, ha='left', va='top', fontsize=10, bbox=dict(facecolor='white', alpha=0.6, edgecolor='none'))

    matches = (best or {}).get('matches', []) or []
    try:
        wave_ref = np.array([m.get('wave_ref', np.nan) for m in matches], dtype=float)
        wave_resid = np.array([m.get('wave_resid', np.nan) for m in matches], dtype=float)
    except Exception:
        wave_ref = np.array([], dtype=float)
        wave_resid = np.array([], dtype=float)
    mfin = np.isfinite(wave_ref) & np.isfinite(wave_resid)
    if np.any(mfin):
        ax_bot.axhline(0.0, color='0.5', lw=1.0)
        ax_bot.scatter(wave_ref[mfin], wave_resid[mfin], s=18, c='tab:purple', alpha=0.8)
        try:
            rms = float((best or {}).get('rms', np.nan))
            ax_bot.text(0.02, 0.90, f"RMS={rms:.3f}", transform=ax_bot.transAxes, ha='left', va='top', fontsize=10)
        except Exception:
            pass
    else:
        ax_bot.text(0.5, 0.5, 'No matched lines', ha='center', va='center', color='0.5')
    ax_bot.set_xlabel('Reference wavelength')
    ax_bot.set_ylabel('Residual (fit - ref)')
    ax_bot.grid(alpha=0.2)

    fig.tight_layout(rect=(0.02, 0.02, 1, 1))
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    out = out_folder / filename
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def plot_trace_overlay(
    out_folder: Path,
    xchunks: Optional[np.ndarray],
    trace_chunks: Optional[np.ndarray],
    trace: Optional[np.ndarray],
    filename: str = "trace_chunks.png",
    title: str = "Trace chunks diagnostic",
    fibers: Optional[List[int]] = None,
) -> Optional[Path]:
    """Diagnostic plot of trace chunk positions vs. full trace for selected fibers.

    For each selected fiber, subtract the mean of that fiber's full trace from
    both datasets; scatter-plot (xchunks, trace_chunks[f]) and overlay the full
    trace line (xpix, trace[f]).
    """
    if xchunks is None or trace_chunks is None or trace is None:
        return None
    xc = np.asarray(xchunks, dtype=float).ravel()
    Trc = np.asarray(trace_chunks, dtype=float)
    Tr = np.asarray(trace, dtype=float)
    if Trc.ndim != 2 or Tr.ndim != 2 or xc.ndim != 1:
        return None
    Nfib_c, Nch = Trc.shape
    Nfib, Npix = Tr.shape
    if Nfib == 0 or Npix == 0 or Nch == 0:
        return None
    if xc.size != Nch:
        try:
            xc = xc[:Nch]
        except Exception:
            return None

    if fibers is None:
        fibers = [3, 55, 110]
    fibers = sorted(set(int(max(0, min(f, Nfib - 1))) for f in fibers))
    if len(fibers) == 0:
        fibers = [min(0, Nfib - 1)]

    xpix = np.arange(Npix, dtype=float)

    fig, ax = plt.subplots(1, 1, figsize=(11.0, 4.2))
    colors = plt.cm.tab10(np.linspace(0, 1, max(10, len(fibers))))
    for k, f in enumerate(fibers):
        y_full = Tr[f]
        if not np.isfinite(y_full).any():
            continue
        mu = float(np.nanmean(y_full)) if np.isfinite(y_full).any() else 0.0
        y_off = y_full - mu
        y_chunks = Trc[f]
        y_chunks_off = y_chunks - mu
        c = colors[k % colors.shape[0]]
        m_full = np.isfinite(y_off)
        if np.count_nonzero(m_full) > 3:
            ax.plot(xpix[m_full], y_off[m_full], '-', lw=1.0, alpha=0.9, color=c, label=f"fiber {f} trace")
        m_chunks = np.isfinite(y_chunks_off) & np.isfinite(xc)
        if np.count_nonzero(m_chunks) > 0:
            ax.scatter(xc[m_chunks], y_chunks_off[m_chunks], s=22, marker='o', facecolors='none', edgecolors=c, alpha=0.9, label=f"fiber {f} chunks")

    ax.set_xlim(0, max(1, Npix - 1))
    y_all: List[np.ndarray] = []
    for f in fibers:
        if 0 <= f < Nfib:
            y_full = Tr[f]
            if np.isfinite(y_full).any():
                mu = float(np.nanmean(y_full))
                y_all.append(y_full - mu)
            y_chunks = Trc[f] if f < Nfib_c else None
            if y_chunks is not None:
                y_all.append(y_chunks - mu)
    if len(y_all):
        ycat = np.concatenate([a[np.isfinite(a)] for a in y_all if a is not None])
        if ycat.size:
            lim = np.nanpercentile(np.abs(ycat), 98)
            if np.isfinite(lim) and lim > 0:
                ax.set_ylim(-1.1 * lim, 1.1 * lim)
    ax.set_xlabel('Spectral pixel (x)')
    ax.set_ylabel('Row offset (pixels) [mean-subtracted]')
    ax.set_title(title)
    ax.grid(alpha=0.25)
    ax.legend(loc='upper right', ncol=2, fontsize=9, frameon=False)

    fig.tight_layout()
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    out = out_folder / filename
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out
