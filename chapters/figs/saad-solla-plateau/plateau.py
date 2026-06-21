# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "matplotlib", "tqdm"]
# ///
r"""Saad & Solla plateau figure for chapters/saad_and_solla.tex.

Two separate panels, one PDF each:
  (a) Delta = 0       -> the excess risk descends through the learning phases
                         toward ~0; no asymptotic plateau.
  (b) Delta > 0       -> same early phases, but the SGD variance term Psi pins
                         the excess risk at a finite *noise plateau*.

In each panel we overlay:
  * the deterministic Saad & Solla ODE, integrated from the exact closed-form
    erf expectations (no quadrature anywhere), and
  * a few finite-d online-SGD simulations on the actual weights.

Plotted quantity: the excess population risk
    R(Omega, a) = E[ 1/2 (h - h*)^2 ]                                   (*)
which is exactly the full population risk minus Delta/2 (the irreducible label
-noise floor).  Setting: matched erf soft committee, k = p = 2, second layer
trained.  sigma(x) = erf(x/sqrt(2)).

The closed-form integrals I2 / I2_noise / I3 / I4 are transcribed verbatim from
the reference implementation
    arn4/largebatch-ss : giant-learning/giant_learning/erf_erf_integrals.cpp
and the ODE right-hand side mirrors giant_learning/base.py::overlap_update.
Their `I2` equals asin(rho)/pi = 1/2 of the genuine E[sigma sigma] = (2/pi)asin;
that 1/2 lives only in the risk readout `erf_error`, so the genuine excess risk
(*) is 2 * erf_error(.., noise=0).  The dynamics (I3, I4, I2_noise) are genuine.

The (expensive) numerics for each panel are cached under cache/, keyed by a hash
of every parameter that affects them, so reruns reuse them and only the plotting
is redone.  Set PLATEAU_FORCE=1 to recompute, or delete the cache/ folder.

Run:
    uv run --script chapters/figs/saad-solla-plateau/plateau.py
    PLATEAU_FORCE=1 uv run --script chapters/figs/saad-solla-plateau/plateau.py
    uv run --script chapters/figs/saad-solla-plateau/plateau.py --verify /tmp/largebatch-ss/giant-learning
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
from numpy import arcsin, sqrt, pi
from scipy.integrate import solve_ivp
from tqdm import tqdm


# ----------------------------------------------------------------------------
# Closed-form erf Gaussian integrals  (verbatim from erf_erf_integrals.cpp)
# ----------------------------------------------------------------------------
def I2(C11, C12, C22):
    """E[sigma(z1) sigma(z2)] / 2  for sigma = erf(./sqrt 2)  (reference convention)."""
    return arcsin(C12 / sqrt((1.0 + C11) * (1.0 + C22))) / pi


def I2_noise(C11, C12, C22):
    """E[sigma'(z1) sigma'(z2)]  for sigma = erf(./sqrt 2)."""
    return 2.0 / (pi * sqrt(1.0 + C11 + C22 + C11 * C22 - C12 * C12))


def I3(C11, C12, C13, C22, C23, C33):
    """E[sigma'(z1) z2 sigma(z3)]  (z1: derivative, z2: linear, z3: plain)."""
    L3 = (1.0 + C11) * (1.0 + C33) - C13 * C13
    return 2.0 * (C23 * (1.0 + C11) - C12 * C13) / (pi * (1.0 + C11) * sqrt(L3))


def I4(C11, C12, C13, C14, C22, C23, C24, C33, C34, C44):
    """E[sigma(z1) sigma(z2) sigma'(z3) sigma'(z4)]."""
    L4 = (1.0 + C11) * (1.0 + C22) - C12 * C12
    L0 = (L4 * C34 - C23 * C24 * (1.0 + C11) - C13 * C14 * (1.0 + C22)
          + C12 * C13 * C24 + C12 * C14 * C23)
    L1 = (L4 * (1.0 + C33) - C23 * C23 * (1.0 + C11) - C13 * C13 * (1.0 + C22)
          + 2.0 * C12 * C13 * C23)
    L2 = (L4 * (1.0 + C44) - C24 * C24 * (1.0 + C11) - C14 * C14 * (1.0 + C22)
          + 2.0 * C12 * C14 * C24)
    arg = L0 / sqrt(L1 * L2)
    return (4.0 / (pi * pi * sqrt(L4))) * arcsin(np.clip(arg, -1.0, 1.0))


# ----------------------------------------------------------------------------
# Expected-value building blocks  (verbatim port of erf_erf.pyx::erf_updates)
# ----------------------------------------------------------------------------
def erf_updates(Q, M, P, a, noise):
    """Return (expected_I3_target, expected_I3_network, expected_I4).

    expected_I3_target[j, r] = E[(h* - h) sigma'(lambda_j) lambda*_r]
    expected_I3_network[j, l] = E[(h* - h) sigma'(lambda_j) lambda_l]
    expected_I4[j, l]         = E[((h* - h)^2 + noise) sigma'(lambda_j) sigma'(lambda_l)]
    """
    p = Q.shape[0]
    k = P.shape[0]
    op, ok = 1.0 / p, 1.0 / k
    eI3_target = np.zeros((p, k))
    eI3_network = np.zeros((p, p))
    eI4 = np.zeros((p, p))

    for j in range(p):
        for r in range(k):
            for l in range(p):  # student
                eI3_target[j, r] -= op * a[l] * I3(
                    Q[j, j], M[j, r], Q[j, l], P[r, r], M[l, r], Q[l, l])
            for s in range(k):  # teacher
                eI3_target[j, r] += ok * I3(
                    Q[j, j], M[j, r], M[j, s], P[r, r], P[r, s], P[s, s])

    for j in range(p):
        for l in range(p):
            for m in range(p):  # student
                eI3_network[j, l] -= op * a[m] * I3(
                    Q[j, j], Q[j, l], Q[j, m], Q[l, l], Q[l, m], Q[m, m])
            for r in range(k):  # teacher
                eI3_network[j, l] += ok * I3(
                    Q[j, j], Q[j, l], M[j, r], Q[l, l], M[l, r], P[r, r])

            eI4[j, l] += noise * I2_noise(Q[j, j], Q[j, l], Q[l, l])
            for o in range(p):  # student-student
                for q in range(p):
                    eI4[j, l] += op * op * a[o] * a[q] * I4(
                        Q[j, j], Q[j, l], Q[j, o], Q[j, q],
                        Q[l, l], Q[l, o], Q[l, q], Q[o, o], Q[o, q], Q[q, q])
            for o in range(p):  # student-teacher
                for r in range(k):
                    eI4[j, l] -= 2.0 * op * ok * a[o] * I4(
                        Q[j, j], Q[j, l], Q[j, o], M[j, r],
                        Q[l, l], Q[l, o], M[l, r], Q[o, o], M[o, r], P[r, r])
            for r in range(k):  # teacher-teacher
                for s in range(k):
                    eI4[j, l] += ok * ok * I4(
                        Q[j, j], Q[j, l], M[j, r], M[j, s],
                        Q[l, l], M[l, r], M[l, s], P[r, r], P[r, s], P[s, s])

    return eI3_target, eI3_network, eI4


def erf_error(Q, M, P, a, noise):
    """Reference risk readout (verbatim port of erf_erf.pyx::erf_error).

    Returns 1/4 E[(h - h*)^2] + noise / 2  (their convention; see module docstring).
    """
    p = Q.shape[0]
    k = P.shape[0]
    op, ok = 1.0 / p, 1.0 / k
    risk = 0.0
    for j in range(k):  # teacher-teacher
        for l in range(k):
            risk += ok * ok * I2(P[j, j], P[j, l], P[l, l])
    for j in range(p):  # teacher-student
        for l in range(k):
            risk -= 2.0 * op * ok * a[j] * I2(Q[j, j], M[j, l], P[l, l])
    for j in range(p):  # student-student
        for l in range(p):
            risk += op * op * a[j] * a[l] * I2(Q[j, j], Q[j, l], Q[l, l])
    risk += noise
    return risk / 2.0


def excess_risk(Q, M, P, a):
    """Genuine excess population risk  R(Omega, a) = E[1/2 (h - h*)^2].

    = full population risk - Delta/2.  Equals 2 * erf_error(.., noise=0) because
    the reference I2 carries a factor 1/2 relative to the true E[sigma sigma].
    """
    return 2.0 * erf_error(Q, M, P, a, 0.0)


# ----------------------------------------------------------------------------
# Saad & Solla ODE right-hand side  (mirrors base.py::overlap_update)
# ----------------------------------------------------------------------------
def _I2_genuine(C11, C12, C22):
    return 2.0 * I2(C11, C12, C22)  # = (2/pi) arcsin -> true E[sigma sigma]


def ss_rhs(Q, M, P, a, astar, gamma, noise):
    """dQ/dt, dM/dt, da/dt for the matched erf committee (P fixed, second layer trained)."""
    p, k = Q.shape[0], P.shape[0]
    T, G, I4full = erf_updates(Q, M, P, a, noise)

    dM = a[:, None] * T
    dQ = (a[:, None] * G + a[None, :] * G.T
          + (gamma / p) * np.outer(a, a) * I4full)

    da = np.zeros(p)
    for j in range(p):
        teacher = sum(astar[r] * _I2_genuine(Q[j, j], M[j, r], P[r, r]) for r in range(k))
        student = sum(a[l] * _I2_genuine(Q[j, j], Q[j, l], Q[l, l]) for l in range(p))
        da[j] = teacher / k - student / p
    return dQ, dM, da


def integrate_ode(Q0, M0, P, a0, astar, gamma, noise, t_eval,
                  progress=False, desc="ODE"):
    """Integrate the SS-ODE and return excess risk at the requested times.

    `solve_ivp` runs in one shot, so the progress bar is driven from the RHS:
    it tracks how far (in log-time) the integrator has advanced toward T.
    """
    p, k = Q0.shape[0], P.shape[0]

    def pack(Q, M, a):
        return np.concatenate([Q.ravel(), M.ravel(), a])

    def unpack(y):
        Q = y[:p * p].reshape(p, p)
        M = y[p * p:p * p + p * k].reshape(p, k)
        a = y[p * p + p * k:]
        return Q, M, a

    t0, t1 = float(t_eval[0]), float(t_eval[-1])
    logt0, logt1 = np.log(t0), np.log(t1)
    pbar = tqdm(total=1000, desc=desc, leave=False, disable=not progress,
                bar_format="{l_bar}{bar}| {elapsed}")
    state = {"n": 0}

    def rhs(t, y):
        if progress and t > t0:
            frac = (np.log(t) - logt0) / (logt1 - logt0)
            n = int(1000 * min(max(frac, 0.0), 1.0))
            if n > state["n"]:
                pbar.update(n - state["n"])
                state["n"] = n
        Q, M, a = unpack(y)
        Q = 0.5 * (Q + Q.T)  # keep Q symmetric against round-off
        dQ, dM, da = ss_rhs(Q, M, P, a, astar, gamma, noise)
        return pack(dQ, dM, da)

    sol = solve_ivp(rhs, (t_eval[0], t_eval[-1]), pack(Q0, M0, a0),
                    t_eval=t_eval, method="LSODA", rtol=1e-8, atol=1e-10,
                    max_step=(t_eval[-1] - t_eval[0]) / 50.0)
    pbar.update(1000 - state["n"])
    pbar.close()
    risks = np.empty(len(sol.t))
    for i in range(len(sol.t)):
        Q, M, a = unpack(sol.y[:, i])
        risks[i] = excess_risk(Q, M, P, a)
    return sol.t, risks


# ----------------------------------------------------------------------------
# Finite-d online-SGD simulation on the weights  (chapter Eqs. 12, 14)
# ----------------------------------------------------------------------------
def sigma(x):
    from scipy.special import erf
    return erf(x / sqrt(2.0))


def sigma_prime(x):
    return sqrt(2.0 / pi) * np.exp(-x * x / 2.0)


def orthonormal_teacher(k, d, rng):
    """Teacher rows with ||w*_r||^2/d = 1 and mutually orthogonal, so P = I_k exactly."""
    A = rng.standard_normal((k, d))
    Qr, _ = np.linalg.qr(A.T)          # columns orthonormal, shape (d, k)
    return Qr.T * sqrt(d)              # rows orthonormal * sqrt(d)


def simulate_sgd(d, p, k, gamma, noise, astar, Wstar, W0, a0, t_record, seed,
                 progress=False, desc="SGD"):
    """One online-SGD run; return excess risk measured at the recorded times.

    The run starts from the *given* weights W0 (and a0), so it shares the exact
    initial condition of the ODE; `seed` only drives the per-step sample noise.
    Order parameters are measured from the weights and mapped to the excess risk
    through the same closed form used by the ODE (the Saad & Solla concentration
    statement), so the dots sit directly on the deterministic curve.
    """
    rng = np.random.default_rng(seed)   # per-step samples (x, label noise) only
    sqd = sqrt(d)
    W = W0.copy()                       # exact ODE initial condition
    a = a0.copy()
    P = (Wstar @ Wstar.T) / d          # = I_k by construction

    nu_record = np.unique(np.round(t_record * p * d / gamma).astype(np.int64))
    nu_record = nu_record[nu_record >= 0]
    nu_max = int(nu_record[-1])

    ts, risks = [], []
    Q0 = M0 = a_init = None
    rec = set(nu_record.tolist())
    steps = tqdm(range(nu_max + 1), desc=desc, leave=False, disable=not progress,
                 unit="step", mininterval=0.3)
    for nu in steps:
        if nu in rec:
            Q = (W @ W.T) / d
            Mo = (W @ Wstar.T) / d
            if Q0 is None:
                Q0, M0, a_init = Q.copy(), Mo.copy(), a.copy()
            ts.append(nu * gamma / (p * d))
            risks.append(excess_risk(Q, Mo, P, a))
        # one online-SGD step
        x = rng.standard_normal(d)
        lam = (W @ x) / sqd
        lams = (Wstar @ x) / sqd
        h = np.dot(a, sigma(lam)) / p
        hstar = np.dot(astar, sigma(lams)) / k
        err = hstar + sqrt(noise) * rng.standard_normal() - h
        coeff = (gamma / (p * sqd)) * err * a * sigma_prime(lam)   # (p,)
        W += coeff[:, None] * x[None, :]
        a += (gamma / (p * d)) * err * sigma(lam)
    return np.array(ts), np.array(risks), Q0, M0, a_init


# ----------------------------------------------------------------------------
# Oracle verification against giant_learning (MonteCarloOverlaps, build-free)
# ----------------------------------------------------------------------------
def verify(giant_learning_dir, mc_size=300_000, n_trials=3):
    """Check my closed forms against giant_learning's Monte-Carlo reference.

    The reference MonteCarloOverlaps.__init__ has a latent signature bug, so we
    build the object via __new__ and set only the attributes its methods use
    (the MC logic itself is unchanged giant_learning code).
    """
    sys.path.insert(0, giant_learning_dir)
    from giant_learning.montecarlo_overlaps import MonteCarloOverlaps
    from scipy.special import erf as _erf

    def target(lf):
        return np.mean(_erf(lf / sqrt(2.0)), axis=-1)

    def act(x):
        return _erf(x / sqrt(2.0))

    def actp(x):
        return sqrt(2.0 / pi) * np.exp(-x * x / 2.0)

    def make_mc(Q, M, P, a, noise, seed):
        mc = MonteCarloOverlaps.__new__(MonteCarloOverlaps)
        mc.target, mc.activation, mc.activation_derivative = target, act, actp
        mc.p, mc.k, mc.noise = Q.shape[0], P.shape[0], noise
        mc.lazy_memory = False
        mc.Qs, mc.Ms, mc.a_s, mc.P = [Q], [M], [a], P   # Q/M/a are read-only properties
        mc.rng = np.random.default_rng(seed)
        mc.mc_size = mc_size
        mc.network = lambda lf: (1.0 / mc.p) * np.dot(mc.a, mc.activation(lf))
        return mc

    def rel(x, y):
        return float(np.max(np.abs(np.asarray(x) - np.asarray(y)))
                     / (np.max(np.abs(np.asarray(y))) + 1e-12))

    rng = np.random.default_rng(0)
    p, k = 2, 2
    max_rel = 0.0
    for trial in range(n_trials):
        W = rng.standard_normal((p, p))
        Q = W @ W.T + np.eye(p)            # SPD
        M = 0.3 * rng.standard_normal((p, k))
        P = np.eye(k)
        a = rng.uniform(0.5, 1.5, p)
        noise = float(rng.uniform(0.0, 0.3))

        mc = make_mc(Q, M, P, a, noise, trial)
        mc_T, mc_G, mc_I4 = mc.compute_expected_values()
        mc_err = mc.error()                 # genuine  1/2 E[(h-h*)^2] + noise/2

        T, G, I4f = erf_updates(Q, M, P, a, noise)
        my_full_risk = excess_risk(Q, M, P, a) + noise / 2.0

        rT, rG, rI4 = rel(T, mc_T), rel(G, mc_G), rel(I4f, mc_I4)
        rErr = abs(my_full_risk - mc_err) / (abs(mc_err) + 1e-12)
        max_rel = max(max_rel, rT, rG, rI4, rErr)
        print(f"trial {trial}: noise={noise:.3f}  rel(I3_target)={rT:.2e} "
              f"rel(I3_net)={rG:.2e} rel(I4)={rI4:.2e} rel(risk)={rErr:.2e}")
    tol = 4.0 / sqrt(mc_size)               # ~ MC sampling error
    print(f"\nmax relative deviation vs giant_learning MonteCarlo = {max_rel:.2e}"
          f"  (tol {tol:.2e})")
    ok = max_rel < max(tol, 8e-3)
    print("PASS" if ok else "FAIL (check formulas)")
    return ok


# ----------------------------------------------------------------------------
# Figure
# ----------------------------------------------------------------------------
CONFIG = dict(
    d_list=[2000],                    # d values overlaid in each panel (each its own ODE + SGD)
    colors=["C0", "C1", "C2", "C3"],  # one color per d
    p=8,
    k=4,
    gamma=2.0,
    n_seeds=1,                     # a single simulation, drawn as dots
    T=1e4,
    n_record=50,                   # number of dots along the curve
    teacher_seed=12,
    a0_value=1.0,
    plateau_window=(15.0, 90.0),   # t-range used to read off the symmetric-plateau level
    progress=True,                 # show tqdm bars for the sims and the ODE
    # One entry per output figure.  xlim / ylim are independent per panel
    # (set either to None to autoscale that axis; a (lo, hi) pair with one
    # entry None fixes only that side).
    panels=[
        dict(delta=0.0, fname="plateau-noiseless.pdf",
             title=r"(a) no label noise, $\Delta=0$",
             xlim=(5e-4, 1e4), ylim=(1e-6, 0.25)),
        dict(delta=1e-3, fname="plateau-noisy.pdf",
             title=r"(b) label noise $\Delta=10^{-3}$",
             xlim=(5e-4, 1e4), ylim=(1e-5, 0.25)),
    ],
)


# ----------------------------------------------------------------------------
# Run persistence: cache the (expensive) numerics so reruns are instant
# ----------------------------------------------------------------------------
CACHE_VERSION = 2   # bump to invalidate every cache when the numerics change


def compute_panel_data(delta, cfg):
    """Run the ODE integration and the SGD simulations for one panel.

    Returns the arrays the plot needs -- this is the expensive part the cache
    stores.  All seeds share the same recording grid, so the SGD risks are
    returned stacked as one (n_seeds, n_record) array.
    """
    d, p, k = cfg["d"], cfg["p"], cfg["k"]
    gamma = cfg["gamma"]
    progress = cfg.get("progress", True)
    astar = np.ones(k)
    a0 = np.full(p, cfg["a0_value"])

    rng = np.random.default_rng(cfg["teacher_seed"])
    Wstar = orthonormal_teacher(k, d, rng)
    P = (Wstar @ Wstar.T) / d

    # log-spaced sampling times (strictly positive: t=0 would break the log axis)
    t_record = np.unique(np.geomspace(gamma / (p * d), cfg["T"], cfg["n_record"]))

    # initial student weights, shared by the ODE and the SGD run so they start
    # from exactly the same (Q0, M0, a0)
    rng_init = np.random.default_rng(cfg["teacher_seed"] + 1)
    W_rep = rng_init.standard_normal((p, d))
    Q0 = (W_rep @ W_rep.T) / d
    M0 = (W_rep @ Wstar.T) / d
    t_ode = np.geomspace(1e-3, cfg["T"], 700)
    t_ode, r_ode = integrate_ode(Q0, M0, P, a0, astar, gamma, delta, t_ode,
                                 progress=progress, desc=f"ODE  Δ={delta:g}")

    # SGD simulation(s) launched from the very same weights W_rep
    sim_ts, sim_rs = np.empty(0), []
    for s in range(cfg["n_seeds"]):
        ts, rs, _, _, _ = simulate_sgd(
            d, p, k, gamma, delta, astar, Wstar, W_rep, a0, t_record, seed=100 + s,
            progress=progress, desc=f"SGD  Δ={delta:g}  seed {s + 1}/{cfg['n_seeds']}")
        sim_ts = ts
        sim_rs.append(rs)
    sim_rs = np.array(sim_rs) if sim_rs else np.empty((0, sim_ts.size))
    return dict(t_ode=t_ode, r_ode=r_ode, sim_ts=sim_ts, sim_rs=sim_rs)


def _cache_blob(delta, cfg):
    """Canonical JSON of every parameter that affects the numerics (the cache key)."""
    params = dict(
        version=CACHE_VERSION, delta=float(delta),
        d=cfg["d"], p=cfg["p"], k=cfg["k"], gamma=cfg["gamma"], T=cfg["T"],
        n_record=cfg["n_record"], n_seeds=cfg["n_seeds"],
        teacher_seed=cfg["teacher_seed"], a0_value=cfg["a0_value"],
    )
    return json.dumps(params, sort_keys=True)


def _ensure_cache_dir(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    gi = os.path.join(cache_dir, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as fh:
            fh.write("# Cached run results, regenerable by plateau.py.\n*\n!.gitignore\n")


def cached_panel_data(delta, cfg, cache_dir, force=False):
    """Load this panel's numerics from `cache_dir`, or compute and store them.

    The cache key hashes every parameter that affects the numerics, so any change
    (d, p, k, gamma, T, n_seeds, delta, ...) transparently triggers a recompute,
    while appearance-only edits (axis limits, labels) reuse the cache.
    """
    blob = _cache_blob(delta, cfg)
    h = hashlib.sha1(blob.encode()).hexdigest()[:10]
    name = f"panel_d{cfg['d']}_p{cfg['p']}_k{cfg['k']}_delta{float(delta):g}_{h}.npz"
    path = os.path.join(cache_dir, name)

    if not force and os.path.exists(path):
        try:
            z = np.load(path, allow_pickle=False)
            if z["params_json"].item() == blob:
                print(f"[cache] hit   {name}")
                return {key: z[key] for key in ("t_ode", "r_ode", "sim_ts", "sim_rs")}
            print(f"[cache] stale {name} -> recomputing")
        except Exception as exc:          # corrupt / unreadable cache -> recompute
            print(f"[cache] unreadable {name} ({exc}) -> recomputing")
    else:
        print(f"[cache] {'forced ' if force else 'miss   '}{name} -> computing")

    data = compute_panel_data(delta, cfg)
    _ensure_cache_dir(cache_dir)
    np.savez_compressed(path, params_json=blob, **data)
    print(f"[cache] saved {name}")
    return data


def make_panel(ax, datasets, xlim=None, ylim=None):
    """Overlay several d on one panel.

    `datasets` is a list of (d, color, data) triples; each contributes its own
    deterministic ODE (solid line) and its single SGD run (dots), in `color`.
    """
    for idx, (d, color, data) in enumerate(datasets):
        ode_label = r"Saad \& Solla ODE" if idx == 0 else None
        ax.plot(data["t_ode"], data["r_ode"], color=color, lw=1.8, zorder=2,
                label=ode_label)
        sim_rs = data["sim_rs"]
        for i in range(sim_rs.shape[0]):
            sgd_label = rf"online SGD ($d={d}$)" if (idx == 0 and i == 0) else None
            ax.plot(data["sim_ts"], sim_rs[i], color=color, linestyle="none",
                    marker="o", ms=4.0, mec="white", mew=0.5, zorder=3,
                    label=sgd_label)

    ax.set_xscale("log")
    ax.set_yscale("log")
    if xlim is not None:
        ax.set_xlim(*xlim)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.set_xlabel(r"training time $t = \nu\,\gamma/(p\,d)$")
    ax.set_ylabel(r"excess risk $\;\mathcal{R}-\Delta/2$")
    ax.grid(True, which="both", ls=":", lw=0.4, alpha=0.5)
    ax.legend(frameon=False, fontsize=9, loc="lower left")


def main():
    import matplotlib
    # The pgf backend compiles each figure through (pdf)latex, so usetex text is
    # embedded as proper vector glyphs.  The default Agg->PDF path drops the math
    # minus sign (a known usetex+PDF Type1 bug), which is why we use pgf here.
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
    if os.environ.get("PLATEAU_NSEEDS") is not None:   # fast preview override
        cfg["n_seeds"] = int(os.environ["PLATEAU_NSEEDS"])
    force = os.environ.get("PLATEAU_FORCE") is not None   # set to recompute
    outdir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(outdir, "cache")

    for panel in cfg["panels"]:
        delta = panel["delta"]
        datasets = []
        for d, color in zip(cfg["d_list"], cfg["colors"]):
            cfg_d = dict(cfg)
            cfg_d["d"] = d                 # cached_panel_data keys on cfg["d"]
            data = cached_panel_data(delta, cfg_d, cache_dir, force=force)
            datasets.append((d, color, data))
        fig, ax = plt.subplots(figsize=(3.8, 3.0))
        make_panel(ax, datasets, xlim=panel.get("xlim"), ylim=panel.get("ylim"))
        fig.tight_layout()
        path = os.path.join(outdir, panel["fname"])
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        print("wrote", path)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--verify":
        mc_size = int(sys.argv[3]) if len(sys.argv) >= 4 else 300_000
        ok = verify(sys.argv[2], mc_size=mc_size)
        sys.exit(0 if ok else 1)
    main()
