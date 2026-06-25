# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
r"""Correlation-potential and golf-course figures for chapters/exponents.tex.

Five PDFs:
  ell1.pdf    -- ℓ=1: He1--He1 (linear) and erf--erf
  ell2.pdf    -- ℓ=2: He2--He2 and phase retrieval (x²--x²)
  ell3.pdf    -- ℓ=3: He3--He3
  rm.pdf      -- population risk R(m) = 2(1-m²) for phase retrieval
  sphere.pdf  -- deformed 3-D sphere illustrating the mediocrity saddle:
                 spike at poles (teacher directions), flat equatorial belt,
                 narrow blue cap showing how hard it is to find the pole by
                 random initialisation in high dimensions.

Closed-form expressions:
  He_k--He_k:  C(m) = k! m^k
  x²--x²:      C(m) = 1 + 2m²  (= He2 + constant a₀² term)
  erf--erf:    C(m) = (2/π)arcsin(m/2)
                [from the I2 formula in plateau.py: I2(1,m,1) = arcsin(m/2)/π,
                 and E[σσ] = 2·I2]

Run:
    uv run --script chapters/figs/correlation-potentials/potentials.py
"""
from __future__ import annotations

import os

import numpy as np

# Thesis palette colours
BLUE = "#4F86C6"
AMBER = "#E0922F"

m_vals = np.linspace(-1, 1, 500)


def he1(m):
    return m


def erf_erf(m):
    return (2 / np.pi) * np.arcsin(m / 2)


def he2(m):
    return 2 * m**2


def x2_x2(m):
    return 1 + 2 * m**2


def he3(m):
    return 6 * m**3


def rm_phase(m):
    return 2 * (1 - m**2)


# Each panel: fname, title, curves, optional ylabel and legend_loc.
PANELS = [
    {
        "fname": "ell1.pdf",
        "title": r"$\ell = 1$",
        "curves": [
            {"label": r"$\mathrm{He}_1$--$\mathrm{He}_1$", "fn": he1,      "color": BLUE,  "ls": "-"},
            {"label": r"$\mathrm{erf}$--$\mathrm{erf}$",   "fn": erf_erf,  "color": AMBER, "ls": "--"},
        ],
        "legend_loc": "upper left",
    },
    {
        "fname": "ell2.pdf",
        "title": r"$\ell = 2$",
        "curves": [
            {"label": r"$\mathrm{He}_2$--$\mathrm{He}_2$",           "fn": he2,   "color": BLUE,  "ls": "-"},
            {"label": r"$x^2$--$x^2$ (phase retrieval)", "fn": x2_x2, "color": AMBER, "ls": "--"},
        ],
        "legend_loc": "upper left",
    },
    {
        "fname": "ell3.pdf",
        "title": r"$\ell = 3$",
        "curves": [
            {"label": r"$\mathrm{He}_3$--$\mathrm{He}_3$", "fn": he3, "color": BLUE, "ls": "-"},
        ],
        "legend_loc": "upper left",
    },
    {
        "fname": "rm.pdf",
        "title": r"population risk $\mathcal{R}(m)$",
        "curves": [
            {"label": r"$\mathcal{R}(m) = 2(1-m^2)$", "fn": rm_phase, "color": BLUE, "ls": "-"},
        ],
        "ylabel": r"$\mathcal{R}(m)$",
        # Downward parabola peaks at m=0; upper corners are clear of the curve.
        "legend_loc": "upper right",
    },
]


def make_line_panels(outdir, plt):
    for panel in PANELS:
        fig, ax = plt.subplots(figsize=(3.0, 2.6))

        for curve in panel["curves"]:
            ax.plot(
                m_vals,
                curve["fn"](m_vals),
                color=curve["color"],
                lw=1.8,
                ls=curve["ls"],
                label=curve["label"],
            )

        ax.set_xlabel(r"$m$")
        ax.set_ylabel(panel.get("ylabel", r"$C(m)$"))
        ax.set_title(panel["title"])
        ax.set_xlim(-1, 1)
        ax.axhline(0, color="black", lw=0.6, ls=":")
        ax.axvline(0, color="black", lw=0.6, ls=":")
        ax.grid(True, ls=":", lw=0.4, alpha=0.5)
        ax.legend(frameon=False, fontsize=9, loc=panel.get("legend_loc", "upper left"))

        fig.tight_layout()
        path = os.path.join(outdir, panel["fname"])
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        print("wrote", path)


def make_sphere_figure(outdir, plt):
    """3-D schematic of the loss landscape on S^{d-1}.

    Shape: revolution of (x = A·sin³θ, z = B·cosθ) around the z-axis.
    sin³θ tapers sharply to 0 at the poles while staying near A at the
    equator, giving a wide flat belt (mediocrity, red) and two narrow spikes
    at ±w★ (low risk, blue).  The narrow blue caps show that the favourable
    region is exponentially small in high d.
    """
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    N      = 80
    A_eq   = 2.0   # equatorial radius
    B_pole = 1.5   # pole height
    n_pow  = 3     # tapering exponent: larger → sharper spike

    theta = np.linspace(0.0, np.pi, N)
    phi   = np.linspace(0.0, 2 * np.pi, N)
    T, P  = np.meshgrid(theta, phi)
    m = np.cos(T)

    X = A_eq   * np.sin(T)**n_pow * np.cos(P)
    Y = A_eq   * np.sin(T)**n_pow * np.sin(P)
    Z = B_pole * m

    # Blue cap from |m|=0.80 to 1 (≈ 37° from each pole) — narrow enough to
    # illustrate the high-dimensional effect but wide enough to be clearly visible.
    # Sharp transition over Δm=0.07 so the boundary looks like a definite region.
    # RdBu: 0 → dark red (equator, high risk), 1 → dark blue (pole, low risk).
    m_lo, m_hi = 0.80, 0.87
    cap = np.clip((np.abs(m) - m_lo) / (m_hi - m_lo), 0.0, 1.0)
    face_colors = plt.cm.RdBu(cap)

    fig = plt.figure(figsize=(3.5, 3.5))
    ax  = fig.add_subplot(111, projection="3d")
    ax.plot_surface(
        X, Y, Z,
        facecolors=face_colors,
        alpha=0.92,
        linewidth=0,
        antialiased=True,
        shade=False,
    )

    # Teacher direction: north pole spike tip
    ax.scatter([0], [0], [B_pole],
               color="gold", s=200, zorder=10,
               marker="*", edgecolors="dimgray", linewidth=0.5)
    ax.text(0.10, 0.10, B_pole + 0.13, r"$\vec{w}_\star$", fontsize=11)

    # Random init on the front face of the equatorial belt (phi near viewing dir)
    phi_0   = 0.35
    theta_0 = np.pi / 2 - 0.12   # slightly above equator for visibility
    x0 = A_eq * np.sin(theta_0)**n_pow * np.cos(phi_0)
    y0 = A_eq * np.sin(theta_0)**n_pow * np.sin(phi_0)
    z0 = B_pole * np.cos(theta_0)
    ax.scatter([x0], [y0], [z0], color="white", s=70, zorder=10,
               edgecolors="black", linewidth=1.2)
    ax.text(x0 + 0.05, y0 - 0.30, z0 + 0.15, r"$\vec{w}^0$", fontsize=10)

    # Sphere label
    ax.text2D(0.74, 0.88, r"$\mathbb{S}^{d-1}$",
              transform=ax.transAxes, fontsize=11)

    ax.set_box_aspect([A_eq, A_eq, B_pole])
    ax.set_axis_off()
    ax.view_init(elev=28, azim=20)

    fig.tight_layout()
    path = os.path.join(outdir, "sphere.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("wrote", path)


def main():
    import matplotlib

    # pgf backend: embeds usetex text as proper vector glyphs (avoids the
    # minus-sign drop bug present in the Agg->PDF path).
    matplotlib.use("pgf")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "text.usetex": True,
            "font.family": "serif",
            "font.size": 11,
            "axes.titlesize": 12,
            "axes.linewidth": 0.8,
            "pgf.texsystem": "pdflatex",
            "pgf.rcfonts": False,
            "pgf.preamble": r"\usepackage{amssymb}",
        }
    )

    outdir = os.path.dirname(os.path.abspath(__file__))
    make_line_panels(outdir, plt)
    make_sphere_figure(outdir, plt)


if __name__ == "__main__":
    main()
