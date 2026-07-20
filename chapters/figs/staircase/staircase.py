# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "matplotlib", "tqdm"]
# ///
r"""Staircase-recovery figure for Example 1.3 in chapters/exponents.tex.

We learn a k=3 multi-index teacher with a *matched, multilinear* student and
watch the teacher-overlaps m_r = <w_r, w*_r>/d climb.  The student is fixed,

    f(x)  = phi_1 + phi_1 phi_2 + phi_1 phi_2 phi_3 ,   phi_r = <w_r, x>/sqrt(d)

with three free direction vectors w_1, w_2, w_3 trained by projected (spherical)
online SGD, ||w_r|| = sqrt(d).  Only the TARGET changes between the two panels:

  (a) staircase target   h* = psi_1 + psi_1 psi_2 + psi_1 psi_2 psi_3
        -> the linear rung pins w_1, then w_1 turns phi_1 phi_2 into an effective
           linear drive on w_2, then w_1,w_2 unlock w_3: m_1, m_2, m_3 climb in
           sequence (three saddles) to full recovery.  O(d) samples.
  (b) bare monomial      h* = psi_1 psi_2 psi_3
        -> the target hands the student no lower rungs, so the drift on every m_r
           is O(M^2) at initialization and the overlaps stay pinned near 1/sqrt(d)
           over the same budget.  The student is identical to (a) (it still
           contains phi_1 phi_2 phi_3), so this is not a representability failure
           -- it is the missing staircase structure of the target.  Leap 3, O(d^2).

In each panel we overlay, launched from a shared initial condition:
  * the deterministic order-parameter ODE (the concentration / "Saad & Solla"
    limit -- no permutation symmetry here, but the ODE still closes), whose
    right-hand side is assembled from EXACT Gaussian moments (Isserlis/Wick over
    the six jointly-Gaussian pre-activations phi_1..3, psi_1..3), and
  * a finite-d online-SGD simulation on the actual weights.

The expensive numerics are cached under cache/, keyed by a hash of every
parameter that affects them; reruns reuse them and only replot.  Set
STAIRCASE_FORCE=1 to recompute, or delete cache/.

Run:
    uv run --script chapters/figs/staircase/staircase.py
    uv run --script chapters/figs/staircase/staircase.py --verify
    STAIRCASE_FORCE=1 uv run --script chapters/figs/staircase/staircase.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
from numpy import sqrt
from scipy.integrate import solve_ivp
from tqdm import tqdm

# Variable indexing for the six jointly-Gaussian pre-activations:
#   phi_1,phi_2,phi_3 -> 0,1,2   (student)      psi_1,psi_2,psi_3 -> 3,4,5 (teacher)
K = 3


# ----------------------------------------------------------------------------
# Tiny polynomial algebra over the six Gaussian variables
# A polynomial is a dict {monomial: coeff}, monomial = sorted tuple of var indices
# (with repetition).  () is the constant 1.
# ----------------------------------------------------------------------------
def poly_mul(p, q):
    out = {}
    for a, ca in p.items():
        for b, cb in q.items():
            key = tuple(sorted(a + b))
            out[key] = out.get(key, 0.0) + ca * cb
    return out


def poly_sub(p, q):
    out = dict(p)
    for k, v in q.items():
        out[k] = out.get(k, 0.0) - v
    return {k: v for k, v in out.items() if v != 0.0}


def poly_scale(p, c):
    return {k: v * c for k, v in p.items()}


def poly_add(*ps):
    out = {}
    for p in ps:
        for k, v in p.items():
            out[k] = out.get(k, 0.0) + v
    return {k: v for k, v in out.items() if v != 0.0}


def poly_deriv(p, i):
    """d/d(var i) of a polynomial (handles repeated factors via the power rule)."""
    out = {}
    for mono, c in p.items():
        cnt = mono.count(i)
        if cnt:
            lst = list(mono)
            lst.remove(i)
            key = tuple(sorted(lst))
            out[key] = out.get(key, 0.0) + c * cnt
    return out


def var(i):
    return {(i,): 1.0}


# ----------------------------------------------------------------------------
# Exact Gaussian moments  E[ prod of Gaussians ]  by Isserlis / Wick
# ----------------------------------------------------------------------------
def isserlis(mono, cov, memo):
    """E[ prod_{i in mono} X_i ] for zero-mean Gaussians with covariance `cov`."""
    n = len(mono)
    if n & 1:
        return 0.0
    if n == 0:
        return 1.0
    v = memo.get(mono)
    if v is not None:
        return v
    a = mono[0]
    s = 0.0
    for j in range(1, n):
        rest = mono[1:j] + mono[j + 1:]
        s += cov[a, mono[j]] * isserlis(rest, cov, memo)
    memo[mono] = s
    return s


def expect(poly, cov, memo):
    return sum(c * isserlis(mono, cov, memo) for mono, c in poly.items())


# ----------------------------------------------------------------------------
# Student, targets, and the pre-assembled integrands of the ODE right-hand side
# ----------------------------------------------------------------------------
# Student and staircase target share the same three "rung" coefficients c =
# (c1, c2, c3), so the matched student can reach zero risk:
#   f  = c1 phi_1 + c2 phi_1 phi_2 + c3 phi_1 phi_2 phi_3   (vars 0,1,2)
#   h* = c1 psi_1 + c2 psi_1 psi_2 + c3 psi_1 psi_2 psi_3   (vars 3,4,5)
_STUDENT_RUNGS = [(0,), (0, 1), (0, 1, 2)]
_TARGET_RUNGS = [(3,), (3, 4), (3, 4, 5)]


def _rung_poly(coeffs, rungs):
    p = {}
    for c, mono in zip(coeffs, rungs):
        if c:
            p[mono] = p.get(mono, 0.0) + float(c)
    return p


def student_poly(coeffs):
    return _rung_poly(coeffs, _STUDENT_RUNGS)


def target_poly(coeffs):
    """Staircase target sum_r coeffs[r]*(psi_1..psi_{r+1}); the bare top monomial
    is coeffs = (0, 0, c)."""
    return _rung_poly(coeffs, _TARGET_RUNGS)


def student_eval(phi, c):
    return c[0] * phi[0] + c[1] * phi[0] * phi[1] + c[2] * phi[0] * phi[1] * phi[2]


def student_grad(phi, c):
    """df/dphi_r evaluated at phi (r = 1,2,3)."""
    return np.array([c[0] + c[1] * phi[1] + c[2] * phi[1] * phi[2],
                     c[1] * phi[0] + c[2] * phi[0] * phi[2],
                     c[2] * phi[0] * phi[1]])


def target_fn(coeffs):
    """Scalar teacher label h*(psi) matching `target_poly(coeffs)`."""
    c1, c2, c3 = coeffs

    def h(psi):
        return c1 * psi[0] + c2 * psi[0] * psi[1] + c3 * psi[0] * psi[1] * psi[2]

    return h


def build_rhs_terms(student_coeffs, target_coeffs):
    """Pre-assemble the polynomial integrands of the ODE RHS.

    Returns dicts of {monomial: coeff} for
        driftM[r][s] = E[(h*-f) df/dphi_r * psi_s]
        driftQ[r][s] = E[(h*-f) df/dphi_r * phi_s]
        radial[r]    = E[(h*-f) df/dphi_r * phi_r]
    (expectations taken later against the current covariance).
    """
    f = student_poly(student_coeffs)
    residual = poly_sub(target_poly(target_coeffs), f)   # h* - f
    dfd = [poly_deriv(f, r) for r in range(K)]
    driftM = [[poly_mul(poly_mul(residual, dfd[r]), var(3 + s)) for s in range(K)]
              for r in range(K)]
    driftQ = [[poly_mul(poly_mul(residual, dfd[r]), var(s)) for s in range(K)]
              for r in range(K)]
    radial = [poly_mul(poly_mul(residual, dfd[r]), var(r)) for r in range(K)]
    return driftM, driftQ, radial


def build_risk_polys(student_coeffs, target_coeffs):
    """Polynomials f^2, f*h*, h*^2 for the excess risk R = 1/2 E[(f-h*)^2]."""
    f = student_poly(student_coeffs)
    h = target_poly(target_coeffs)
    return poly_mul(f, f), poly_mul(f, h), poly_mul(h, h)


def risk_from_MQ(M, Q, P, risk_polys):
    ff, fh, hh = risk_polys
    cov = build_cov(M, Q, P)
    memo = {}
    return 0.5 * (expect(ff, cov, memo) - 2.0 * expect(fh, cov, memo) + expect(hh, cov, memo))


def build_cov(M, Q, P):
    """6x6 covariance of (phi_1..3, psi_1..3):  [[Q, M], [M^T, P]]."""
    cov = np.empty((2 * K, 2 * K))
    cov[:K, :K] = Q
    cov[:K, K:] = M
    cov[K:, :K] = M.T
    cov[K:, K:] = P
    return cov


def ss_rhs(M, Q, P, terms):
    """dM/dt, dQ/dt for the matched multilinear student (spherical constraint,
    variance term dropped, as the single-index chapter drops Psi)."""
    driftM, driftQ, radial = terms
    cov = build_cov(M, Q, P)
    memo = {}
    R = np.array([expect(radial[r], cov, memo) for r in range(K)])
    DM = np.array([[expect(driftM[r][s], cov, memo) for s in range(K)] for r in range(K)])
    DQ = np.array([[expect(driftQ[r][s], cov, memo) for s in range(K)] for r in range(K)])
    dM = DM - M * R[:, None]
    dQ = (DQ - Q * R[:, None]) + (DQ.T - Q * R[None, :])
    return dM, dQ


# ----------------------------------------------------------------------------
# Deterministic ODE integration
# ----------------------------------------------------------------------------
def integrate_ode(M0, Q0, P, terms, t_eval, progress=False, desc="ODE"):
    def pack(M, Q):
        return np.concatenate([M.ravel(), Q.ravel()])

    def unpack(y):
        return y[:K * K].reshape(K, K), y[K * K:].reshape(K, K)

    t0, t1 = float(t_eval[0]), float(t_eval[-1])
    pbar = tqdm(total=1000, desc=desc, leave=False, disable=not progress,
                bar_format="{l_bar}{bar}| {elapsed}")
    state = {"n": 0}

    def rhs(t, y):
        if progress and t1 > t0:
            n = int(1000 * min(max((t - t0) / (t1 - t0), 0.0), 1.0))
            if n > state["n"]:
                pbar.update(n - state["n"])
                state["n"] = n
        M, Q = unpack(y)
        Q = 0.5 * (Q + Q.T)
        dM, dQ = ss_rhs(M, Q, P, terms)
        return pack(dM, dQ)

    sol = solve_ivp(rhs, (t0, t1), pack(M0, Q0), t_eval=t_eval,
                    method="LSODA", rtol=1e-8, atol=1e-10,
                    max_step=(t1 - t0) / 200.0)
    pbar.update(1000 - state["n"])
    pbar.close()
    Ms = np.array([unpack(sol.y[:, i])[0] for i in range(sol.t.size)])
    Qs = np.array([0.5 * (unpack(sol.y[:, i])[1] + unpack(sol.y[:, i])[1].T)
                   for i in range(sol.t.size)])
    return sol.t, Ms, Qs


# ----------------------------------------------------------------------------
# Finite-d online-SGD simulation on the weights (projected / spherical)
# ----------------------------------------------------------------------------
def orthonormal_teacher(k, d, rng):
    """Teacher rows with ||w*_r||^2/d = 1 and mutually orthogonal, so P = I_k."""
    A = rng.standard_normal((k, d))
    Qr, _ = np.linalg.qr(A.T)
    return Qr.T * sqrt(d)


def spherical_init(k, d, rng):
    """Random student rows normalized to the sphere ||w_r|| = sqrt(d)."""
    W = rng.standard_normal((k, d))
    return W * (sqrt(d) / np.linalg.norm(W, axis=1))[:, None]


def simulate_sgd(d, gamma, student_coeffs, hfun, Wstar, W0, t_record, seed,
                 progress=False, desc="SGD"):
    """One projected online-SGD run; return the full order parameters (M, Q) at the
    record times.

    Starts from the given weights W0 (shared with the ODE initial condition); the
    time is rescaled t = nu * gamma / d so it matches the deterministic ODE.
    """
    rng = np.random.default_rng(seed)
    sqd = sqrt(d)
    W = W0.copy()

    nu_record = np.unique(np.round(t_record * d / gamma).astype(np.int64))
    nu_record = nu_record[nu_record >= 0]
    rec = set(nu_record.tolist())
    nu_max = int(nu_record[-1])

    ts, Ms, Qs = [], [], []
    steps = tqdm(range(nu_max + 1), desc=desc, leave=False, disable=not progress,
                 unit="step", mininterval=0.3)
    for nu in steps:
        if nu in rec:
            ts.append(nu * gamma / d)
            Ms.append((W @ Wstar.T) / d)
            Qs.append((W @ W.T) / d)
        x = rng.standard_normal(d)
        phi = (W @ x) / sqd
        psi = (Wstar @ x) / sqd
        err = student_eval(phi, student_coeffs) - hfun(psi)   # f - h*
        dfd = student_grad(phi, student_coeffs)
        W -= (gamma / sqd) * (err * dfd)[:, None] * x[None, :]
        W *= (sqd / np.linalg.norm(W, axis=1))[:, None]    # project onto the sphere
    return np.array(ts), np.array(Ms), np.array(Qs)


# ----------------------------------------------------------------------------
# One panel: shared init, ODE + SGD, for a given target
# ----------------------------------------------------------------------------
def compute_panel_data(coeffs, cfg):
    d, gamma, T = cfg["d"], cfg["gamma"], cfg["T"]
    scoeffs = cfg["student_coeffs"]
    progress = cfg.get("progress", True)
    terms = build_rhs_terms(scoeffs, coeffs)
    risk_polys = build_risk_polys(scoeffs, coeffs)
    hfun = target_fn(coeffs)

    rng_t = np.random.default_rng(cfg["teacher_seed"])
    Wstar = orthonormal_teacher(K, d, rng_t)
    P = (Wstar @ Wstar.T) / d                              # = I_K

    rng_i = np.random.default_rng(cfg["teacher_seed"] + 1)
    W0 = spherical_init(K, d, rng_i)
    M0 = (W0 @ Wstar.T) / d
    Q0 = (W0 @ W0.T) / d

    def diag(Ms):
        return np.diagonal(Ms, axis1=1, axis2=2)            # (n, 3): m_r = M_rr
    def risk(Ms, Qs):
        return np.array([risk_from_MQ(Ms[i], Qs[i], P, risk_polys) for i in range(len(Ms))])

    t_ode = np.linspace(0.0, T, 800)
    t_ode, M_ode, Q_ode = integrate_ode(M0, Q0, P, terms, t_ode,
                                        progress=progress, desc=f"ODE  {cfg['tag']}")

    t_rec = np.linspace(0.0, T, cfg["n_record"])
    t_sgd, M_sgd, Q_sgd = simulate_sgd(d, gamma, scoeffs, hfun, Wstar, W0, t_rec,
                                       seed=cfg["sgd_seed"], progress=progress,
                                       desc=f"SGD  {cfg['tag']}")
    return dict(t_ode=t_ode, m_ode=diag(M_ode), r_ode=risk(M_ode, Q_ode),
                t_sgd=t_sgd, m_sgd=diag(M_sgd), r_sgd=risk(M_sgd, Q_sgd))


# ----------------------------------------------------------------------------
# Run persistence
# ----------------------------------------------------------------------------
CACHE_VERSION = 3


def _cache_blob(cfg):
    keys = ("d", "gamma", "T", "n_record", "teacher_seed", "sgd_seed",
            "student_coeffs", "coeffs", "tag")
    params = {k: cfg[k] for k in keys}
    params["version"] = CACHE_VERSION
    return json.dumps(params, sort_keys=True)


def _ensure_cache_dir(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    gi = os.path.join(cache_dir, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as fh:
            fh.write("# Cached run results, regenerable by staircase.py.\n*\n!.gitignore\n")


def cached_panel_data(cfg, cache_dir, force=False):
    blob = _cache_blob(cfg)
    h = hashlib.sha1(blob.encode()).hexdigest()[:10]
    path = os.path.join(cache_dir, f"panel_{cfg['tag']}_d{cfg['d']}_{h}.npz")
    if not force and os.path.exists(path):
        try:
            z = np.load(path, allow_pickle=False)
            if z["params_json"].item() == blob:
                print(f"[cache] hit   {os.path.basename(path)}")
                return {k: z[k] for k in ("t_ode", "m_ode", "r_ode",
                                          "t_sgd", "m_sgd", "r_sgd")}
            print(f"[cache] stale {os.path.basename(path)} -> recomputing")
        except Exception as exc:
            print(f"[cache] unreadable {os.path.basename(path)} ({exc}) -> recomputing")
    else:
        print(f"[cache] {'forced ' if force else 'miss   '}{os.path.basename(path)} -> computing")
    data = compute_panel_data(cfg["coeffs"], cfg)
    _ensure_cache_dir(cache_dir)
    np.savez_compressed(path, params_json=blob, **data)
    print(f"[cache] saved {os.path.basename(path)}")
    return data


# ----------------------------------------------------------------------------
# Figure configuration
# ----------------------------------------------------------------------------
# Rung coefficients shared by the student and the staircase target.  Shrinking the
# higher rungs delays the 2nd/3rd escapes, separating the saddles so the risk shows
# distinct plateaus.  The monolith target is the bare top rung, c3 * z1 z2 z3.
_C = (1.0, 0.6, 0.35)

CONFIG = dict(
    d=4096,
    gamma=0.01,
    T=16.0,
    n_record=30,
    teacher_seed=7,
    sgd_seed=101,
    student_coeffs=_C,
    progress=True,
    panels=[
        dict(tag="target", coeffs=_C),
        dict(tag="monolith", coeffs=(0.0, 0.0, _C[2])),
    ],
    # aspect ~2.02:1 (fits at height=0.49\textwidth); kept physically small so the
    # 11pt fonts render large once scaled into the page.
    output=dict(fname="staircase.pdf", figsize=(5.8, 2.87),
                width_ratios=(2.0, 1.0), risk_ylim=(5e-3, 2.0)),
)

_DIR_COLORS = ["C0", "C1", "C2"]
_DIR_LABELS = [r"$m_1$", r"$m_2$", r"$m_3$"]


_MONO_DASH = (0, (5, 2))


def make_overlap_panel(ax, data_stair, data_mono, ylim=(-0.05, 1.08)):
    """Wide overlap panel: the three teacher overlaps m_1,m_2,m_3 for BOTH targets.

    Colour = direction; line style = target (solid staircase, dashed monomial).
    """
    from matplotlib.lines import Line2D
    for r in range(K):
        # staircase target: solid line + filled circles
        ax.plot(data_stair["t_ode"], data_stair["m_ode"][:, r], color=_DIR_COLORS[r],
                lw=2.0, zorder=3)
        ax.plot(data_stair["t_sgd"], data_stair["m_sgd"][:, r], color=_DIR_COLORS[r],
                ls="none", marker="o", ms=4.5, mec="white", mew=0.5, zorder=4)
        # bare monomial target: dashed line + x markers (all pinned near 0)
        ax.plot(data_mono["t_ode"], data_mono["m_ode"][:, r], color=_DIR_COLORS[r],
                lw=1.3, ls=_MONO_DASH, alpha=0.85, zorder=2)
        ax.plot(data_mono["t_sgd"], data_mono["m_sgd"][:, r], color=_DIR_COLORS[r],
                ls="none", marker="x", ms=3.5, mew=1.0, alpha=0.85, zorder=2)
    ax.set_ylim(*ylim)
    ax.set_xlim(0.0, data_stair["t_ode"][-1])   # flush to the data, no side margins
    ax.set_xlabel(r"training time $t = \nu\,\gamma/d$")
    ax.set_ylabel(r"teacher overlap $m_r$")
    ax.grid(True, ls=":", lw=0.4, alpha=0.5)

    dir_handles = [Line2D([], [], color=_DIR_COLORS[r], lw=2.0) for r in range(K)]
    leg1 = ax.legend(dir_handles, _DIR_LABELS, frameon=False, fontsize=9,
                     loc="center right", bbox_to_anchor=(1.0, 0.60),
                     handlelength=1.5, ncol=1)
    ax.add_artist(leg1)
    case_handles = [Line2D([], [], color="0.25", lw=2.0, ls="-"),
                    Line2D([], [], color="0.25", lw=1.3, ls=_MONO_DASH)]
    ax.legend(case_handles, [r"staircase", r"monomial"],
              frameon=False, fontsize=9, loc="lower right",
              bbox_to_anchor=(1.0, 0.06), handlelength=2.2)


def make_risk_panel(ax, datasets, ylim=None):
    """Small side panel: population risk R = 1/2 E[(f-h*)^2] for both targets.

    Same style convention as the overlap panel: solid staircase, dashed monomial.
    """
    for (label, color, ls, marker, data) in datasets:
        ax.plot(data["t_ode"], data["r_ode"], color=color, lw=1.8, ls=ls,
                zorder=2, label=label)
        # thin the crosses so they don't bury the dashed monomial line
        style = (dict(mec="white", mew=0.4) if marker == "o"
                 else dict(mew=0.9, markevery=2))
        ax.plot(data["t_sgd"], data["r_sgd"], color=color, ls="none",
                marker=marker, ms=3.2, zorder=3, **style)
    ax.set_yscale("log")
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.set_xlim(0.0, datasets[0][-1]["t_ode"][-1])   # flush to the data, no side margins
    ax.set_xlabel(r"training time $t = \nu\,\gamma/d$")
    ax.set_ylabel(r"population risk $\mathcal{R}$")
    ax.grid(True, which="both", ls=":", lw=0.4, alpha=0.5)
    ax.legend(frameon=False, fontsize=8.5, loc="lower left", handlelength=1.6)


def main():
    import matplotlib
    matplotlib.use("pgf")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "text.usetex": True,
        "font.family": "serif",
        "font.size": 11,
        "axes.titlesize": 12,
        "axes.linewidth": 0.8,
        "pgf.texsystem": "pdflatex",
        "pgf.rcfonts": False,
    })

    cfg = dict(CONFIG)
    force = os.environ.get("STAIRCASE_FORCE") is not None
    outdir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(outdir, "cache")

    def save(fig, fname):
        path = os.path.join(outdir, fname)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        print("wrote", path)

    data_by_tag = {}
    for panel in cfg["panels"]:
        cfg_p = dict(cfg)
        cfg_p.update(tag=panel["tag"], coeffs=panel["coeffs"])
        data_by_tag[panel["tag"]] = cached_panel_data(cfg_p, cache_dir, force=force)

    # one wide figure: overlaps of both cases on the left, small risk plot on the right
    out = cfg["output"]
    fig = plt.figure(figsize=out["figsize"], layout="constrained")
    gs = fig.add_gridspec(1, 2, width_ratios=out["width_ratios"])
    ax_over = fig.add_subplot(gs[0, 0])
    ax_risk = fig.add_subplot(gs[0, 1])
    make_overlap_panel(ax_over, data_by_tag["target"], data_by_tag["monolith"])
    make_risk_panel(ax_risk, [
        (r"staircase", "C3", "-", "o", data_by_tag["target"]),
        (r"monomial", "C4", _MONO_DASH, "x", data_by_tag["monolith"]),
    ], ylim=out["risk_ylim"])
    save(fig, out["fname"])


# ----------------------------------------------------------------------------
# Verification: exact Isserlis moments vs Monte Carlo, on the assembled RHS
# ----------------------------------------------------------------------------
def poly_eval(poly, gauss):
    """Evaluate a polynomial on a batch of Gaussian samples `gauss` (shape (b, 6))."""
    acc = np.zeros(gauss.shape[0])
    for mono, c in poly.items():
        term = np.full(gauss.shape[0], c)
        for idx in mono:
            term = term * gauss[:, idx]
        acc += term
    return acc


def verify(mc_size=4_000_000, batch=200_000, n_trials=3, nsigma=5.0):
    """Check ss_rhs (exact Isserlis moments) against Monte-Carlo, judged against
    the MC standard error (these integrands are degree-6 Gaussian polynomials with
    heavy tails, so a fixed relative tolerance is meaningless -- 5 sigma is).

    A valid (M, Q, P) is built from explicit weight vectors so the 6x6 covariance
    is genuinely PSD; the six pre-activations are then drawn directly via Cholesky
    in bounded-memory batches.
    """
    rng = np.random.default_rng(0)
    n = 64                                    # modest ambient dim -> valid PSD cov
    worst_sigma = 0.0
    all_ok = True
    for trial in range(n_trials):
        scoeffs = tuple(rng.uniform(-1.0, 1.0, 3))
        coeffs = tuple(rng.uniform(-1.0, 1.0, 3))
        terms = build_rhs_terms(scoeffs, coeffs)
        f = student_poly(scoeffs)
        residual = poly_sub(target_poly(coeffs), f)
        dfd = [poly_deriv(f, r) for r in range(K)]

        W = rng.standard_normal((K, n))
        W *= (sqrt(n) / np.linalg.norm(W, axis=1))[:, None]
        Ws = orthonormal_teacher(K, n, rng)
        M = (W @ Ws.T) / n
        Q = (W @ W.T) / n
        P = (Ws @ Ws.T) / n
        dM_ex, dQ_ex = ss_rhs(M, Q, P, terms)

        # Per-component estimator polynomials (constants M,Q folded in), so the MC
        # mean is exactly the ss_rhs dM/dQ and the MC std gives its standard error.
        #   dM[r,s] = E[driftM_rs] - M[r,s] E[radial_r]
        #   dQ[r,s] = E[driftQ_rs] - Q[r,s] E[radial_r] + E[driftQ_sr] - Q[r,s] E[radial_s]
        rad = [poly_mul(poly_mul(residual, dfd[r]), var(r)) for r in range(K)]
        driftM = [[poly_mul(poly_mul(residual, dfd[r]), var(3 + s)) for s in range(K)]
                  for r in range(K)]
        driftQ = [[poly_mul(poly_mul(residual, dfd[r]), var(s)) for s in range(K)]
                  for r in range(K)]
        estM = [[poly_add(driftM[r][s], poly_scale(rad[r], -M[r, s]))
                 for s in range(K)] for r in range(K)]
        estQ = [[poly_add(driftQ[r][s], poly_scale(rad[r], -Q[r, s]),
                          driftQ[s][r], poly_scale(rad[s], -Q[r, s]))
                 for s in range(K)] for r in range(K)]

        comps = [("dM", r, s, estM[r][s], dM_ex[r, s]) for r in range(K) for s in range(K)]
        comps += [("dQ", r, s, estQ[r][s], dQ_ex[r, s]) for r in range(K) for s in range(K)]

        L = np.linalg.cholesky(build_cov(M, Q, P))
        ssum = {i: 0.0 for i in range(len(comps))}
        ssq = {i: 0.0 for i in range(len(comps))}
        drawn = 0
        while drawn < mc_size:
            b = min(batch, mc_size - drawn)
            gauss = rng.standard_normal((b, 2 * K)) @ L.T
            for i, (_, _, _, poly, _) in enumerate(comps):
                vals = poly_eval(poly, gauss)
                ssum[i] += vals.sum()
                ssq[i] += (vals * vals).sum()
            drawn += b

        trial_worst = 0.0
        for i, (kind, r, s, _, exact) in enumerate(comps):
            mean = ssum[i] / mc_size
            var_ = max(ssq[i] / mc_size - mean * mean, 0.0)
            se = sqrt(var_ / mc_size)
            dev = abs(exact - mean) / (se + 1e-15)
            trial_worst = max(trial_worst, dev)
        worst_sigma = max(worst_sigma, trial_worst)
        ok = trial_worst < nsigma
        all_ok = all_ok and ok
        print(f"trial {trial}: coeffs=({coeffs[0]:+.2f},{coeffs[1]:+.2f},{coeffs[2]:+.2f})"
              f"  worst deviation = {trial_worst:.2f} sigma   {'ok' if ok else 'OUTLIER'}")
    print(f"\nworst exact-vs-MonteCarlo deviation across all components = {worst_sigma:.2f} sigma"
          f"  (threshold {nsigma:g} sigma)")
    print("PASS" if all_ok else "FAIL (check formulas)")
    return all_ok


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--verify":
        mc = int(sys.argv[2]) if len(sys.argv) >= 3 else 2_000_000
        sys.exit(0 if verify(mc_size=mc) else 1)
    main()
