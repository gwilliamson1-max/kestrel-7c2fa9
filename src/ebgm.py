"""Empirical Bayes disproportionality — MGPS / DuMouchel (1999).

The classic PRR/ROR blow up on tiny denominators: a drug-event pair with 3 cases
and a near-zero background scores PRR in the thousands, which is noise, not signal.
The FDA's Multi-item Gamma Poisson Shrinker (MGPS) fixes this by shrinking each
pair's observed/expected ratio toward the overall mean, in proportion to how little
data supports it. The output is:

  EBGM  — Empirical Bayes Geometric Mean: the shrunk relative-report ratio.
  EB05  — 5th percentile of the posterior. This is the conservative signal score;
          EB05 >= 2 is DuMouchel's recommended threshold and is far more reliable
          than PRR >= 2 because it cannot be inflated by a single lucky small count.

Model (DuMouchel 1999):
  N_i ~ Poisson(mu_i * E_i),   E_i = expected count = (drug_total * event_total) / N
  mu ~ pi * Gamma(a1, b1) + (1-pi) * Gamma(a2, b2)      (2-component mixture prior)
Marginal of N is a 2-component negative-binomial mixture; the 5 hyper-parameters
(a1, b1, a2, b2, pi) are fit once by maximum likelihood across all pairs. Each
pair's posterior for mu is again a 2-component gamma mixture, from which EBGM and
EB05 are read off.

Caveat: a proper MGPS prior is fit over the full drug x event contingency table
including empty cells. This pipeline pulls only the top events per product, so the
prior is fit on the available (mostly non-trivial) pairs — the shrinkage is still
sound and still pulls tiny-count spikes down hard, but the absolute EBGM is
slightly conservative. Documented, intentional.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, psi
from scipy.stats import gamma as gamma_dist


# --------------------------------------------------------------------------- #
# Negative-binomial marginal (a single gamma-Poisson component)
# --------------------------------------------------------------------------- #
def _nb_logpmf(n, alpha, beta, E):
    """log P(N=n) for N ~ Poisson(mu*E), mu ~ Gamma(alpha, beta)."""
    p = np.clip(beta / (beta + E), 1e-12, 1 - 1e-12)   # "success" prob, bounded
    return (gammaln(n + alpha) - gammaln(alpha) - gammaln(n + 1)
            + alpha * np.log(p) + n * np.log1p(-p))


def _unpack(theta):
    a1, b1, a2, b2 = np.exp(theta[:4])         # positivity via log-parameterization
    pi = 1.0 / (1.0 + math.exp(-theta[4]))     # (0,1) via logit
    return a1, b1, a2, b2, pi


def _neg_loglik(theta, n, E):
    a1, b1, a2, b2, pi = _unpack(theta)
    l1 = _nb_logpmf(n, a1, b1, E)
    l2 = _nb_logpmf(n, a2, b2, E)
    m = np.maximum(l1, l2)                      # log-sum-exp for the mixture
    mix = pi * np.exp(l1 - m) + (1 - pi) * np.exp(l2 - m)
    ll = np.sum(m + np.log(mix))
    return -ll if np.isfinite(ll) else 1e12


class MGPS:
    """Fitted MGPS model. Falls back to a single gamma-Poisson prior, then to a
    weak fixed prior, if the 2-component fit is unstable."""

    def __init__(self):
        self.params = None          # (a1,b1,a2,b2,pi)
        self.mode = "unfit"

    def fit(self, counts, expecteds, log=print):
        n = np.asarray(counts, float)
        E = np.asarray(expecteds, float)
        keep = (E > 0) & np.isfinite(E) & np.isfinite(n)
        n, E = n[keep], E[keep]
        if len(n) < 20:
            self.params = (0.2, 0.1, 2.0, 2.0, 0.33)   # weak default prior
            self.mode = "weak_default"
            log(f"[ebgm] only {len(n)} pairs — using weak default prior")
            return self

        # two-component fit, several starts
        starts = [
            np.array([math.log(.2), math.log(.1), math.log(2.), math.log(2.), 0.0]),
            np.array([math.log(.5), math.log(.5), math.log(5.), math.log(5.), .5]),
        ]
        best = None
        for s in starts:
            try:
                res = minimize(_neg_loglik, s, args=(n, E), method="Nelder-Mead",
                               options={"maxiter": 4000, "xatol": 1e-4, "fatol": 1e-2})
                if res.success and (best is None or res.fun < best.fun):
                    best = res
            except Exception:
                continue
        if best is not None and np.isfinite(best.fun):
            self.params = _unpack(best.x)
            self.mode = "mgps_2component"
            log(f"[ebgm] MGPS 2-component prior fit on {len(n)} pairs")
            return self

        # single-component fallback (method of moments on mu = n/E)
        mu = n / E
        m, v = float(np.mean(mu)), float(np.var(mu))
        if v > 0:
            beta = m / v
            alpha = m * beta
        else:
            alpha, beta = 0.2, 0.1
        self.params = (alpha, beta, alpha, beta, 1.0)
        self.mode = "single_component"
        log(f"[ebgm] fell back to single gamma prior on {len(n)} pairs")
        return self

    def _posterior_weights(self, n, E):
        a1, b1, a2, b2, pi = self.params
        l1 = _nb_logpmf(n, a1, b1, E)
        l2 = _nb_logpmf(n, a2, b2, E)
        m = max(l1, l2)
        w1 = pi * math.exp(l1 - m)
        w2 = (1 - pi) * math.exp(l2 - m)
        q = w1 / (w1 + w2)
        return q                                # posterior weight on component 1

    def ebgm_eb05(self, n, E):
        """Return (EBGM, EB05) for one pair. Posterior mu is a gamma mixture:
        q*Gamma(a1+n, b1+E) + (1-q)*Gamma(a2+n, b2+E)."""
        if self.params is None or E <= 0:
            return None, None
        a1, b1, a2, b2, _ = self.params
        q = self._posterior_weights(n, E)
        s1a, s1b = a1 + n, b1 + E               # shape, rate of component 1
        s2a, s2b = a2 + n, b2 + E

        # EBGM = exp(E[log mu]) ; E[log mu] for Gamma(a,b) is psi(a) - ln(b)
        elog = q * (psi(s1a) - math.log(s1b)) + (1 - q) * (psi(s2a) - math.log(s2b))
        ebgm = math.exp(elog)

        # EB05 = 5th percentile of the mixture CDF, via bisection
        def cdf(x):
            return (q * gamma_dist.cdf(x, s1a, scale=1.0 / s1b)
                    + (1 - q) * gamma_dist.cdf(x, s2a, scale=1.0 / s2b))
        lo, hi = 1e-6, max(ebgm * 4, 10.0)
        while cdf(hi) < 0.05:
            hi *= 2
            if hi > 1e6:
                break
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if cdf(mid) < 0.05:
                lo = mid
            else:
                hi = mid
        return round(ebgm, 2), round(0.5 * (lo + hi), 2)
