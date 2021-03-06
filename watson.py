import numpy as np
import datetime

from scipy.special import gamma
# References:
# [1] S. Sra, D. Karp, The multivariate Watson distribution:
#   Maximum-likelihood estimation and other aspects,
#   Journal of Multivariate Analysis 114 (2013) 256-269


def pdf(X, mu, kappa):
    """Evaluates the pdf defined from a Watson distribution

    Parameters
    ----------
    X: Points to evaluate the pdf in with shape (N, p), where N is number of points and p is dimensionality
    mu: Mean
    kappa: Concentration
    """
    (N, p) = X.shape
    cp = gamma(p/2) / (2*np.pi**(p/2) * (kummer(1/2, p/2, kappa)))
    return cp * np.exp(kappa * np.einsum('i,ji->j', mu, X)**2)


def wmm_pdf(X, Mu, Kappa, Pi):
    """Evaluates the pdf defined from a mixture of Watson distributions

    Parameters
    ----------
    X: Points to evaluate the pdf in with shape (N, p), where N is number of points and p is dimensionality
    Mu: List of means
    Kappa: List of concentration
    Pi: List of priors
    """
    return np.sum([pi * pdf(X, mu, kappa) for mu, kappa, pi in zip(Mu, Kappa, Pi)], axis=0)


def wmm_fit(X, k, maxiter=200, tol=1e-4, verbose=False, seed=None, init=None, return_steps=False, gamma=0):
    """Fits a mixture of Watsons

    Parameters
    ----------
    X: Data to base the model on with shape (N, p), where N is number of points and p is dimensionality
    k: Number of kernels
    init: How to initialize the model.
        "random" for random initialization
        "spiral" for initialization based on golden spiral method (only works for 3d)
        a dict with keys 'mu', 'kappa', 'pi' for another predetermined initialization
    """

    np.random.seed(seed)

    # Get dimensions
    (N, p) = X.shape

    # Initialization
    if isinstance(init, dict):
        mu = init['mu'].copy()
        kappa = init['kappa'].copy()
        pi = init['pi'].copy()
    elif isinstance(init, str) and init.lower() == 'spiral':
        assert p == 3
        mu, kappa, pi = golden_spiral_init(k)
    elif isinstance(init, str) and init.lower() == 'random':
        mu, kappa, pi = random_init(k, p)
    else:
        if p == 3:
            mu, kappa, pi = golden_spiral_init(k)
        else:
            mu, kappa, pi = random_init(k, p)

    # Allocation
    llh = np.zeros((maxiter))
    Mu = np.zeros((maxiter, ) + mu.shape)
    Kappa = np.zeros((maxiter, ) + kappa.shape)
    Bounds = np.zeros((maxiter, ) + kappa.shape + (3, ))
    Pi = np.zeros((maxiter, ) + pi.shape)

    iter = 0
    converged = -1

    # EM loop
    while converged < 0:
        beta, llh[iter] = e_step(X, mu, kappa, pi, k, p)
        mu, kappa, pi, bounds = m_step(X, mu, beta, k, N, p, gamma)
        converged = convergence(llh, iter, maxiter, tol, verbose)
        Mu[iter] = mu
        Kappa[iter] = kappa
        Bounds[iter] = bounds
        Pi[iter] = pi
        iter += 1

    llh = llh[:converged+1]
    if return_steps:
        Mu = Mu[:converged+1]
        Kappa = Kappa[:converged+1]
        Bounds = Bounds[:converged+1]
        Pi = Pi[:converged+1]
        return Mu, Kappa, Pi, llh, Bounds

    return Mu[converged], Kappa[converged], Pi[converged], llh, Bounds[converged]


def random_init(k, p):
    # Randomly initialize mu
    mu = np.random.normal(size=(k, p))
    mu = mu/np.linalg.norm(mu, axis=1)[:, np.newaxis]

    # Initalize kappa and pi
    kappa = np.ones(k)
    pi = np.ones(k)/k
    return mu, kappa, pi


def golden_spiral_init(k):
    # Initialise mu based on "The golden spiral method" https://stackoverflow.com/a/44164075/6843855
    indices = np.arange(0, k, dtype=float) + 0.5
    phi = np.arccos(1 - 2*indices/(2*k))
    theta = np.pi * (1 + 5**0.5) * indices
    mu = np.stack((np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)), axis=1)

    # Initalize kappa and pi
    kappa = np.ones(k)
    pi = np.ones(k)/k

    return mu, kappa, pi


def e_step(X, mu, kappa, pi, k, p):
    # Expectation
    num = np.zeros((X.shape[0], k))
    for j in range(k):  # For each component
        cp = gamma(p/2) / (2*np.pi**(p/2) * (kummer(1/2, p/2, kappa[j])))
        # Uses Watson distribution (2.1) [1], compute using 4.3 [1]
        num[:, j] = pi[j] * cp * np.exp(kappa[j] * np.einsum('i,ji->j', mu[j, :], X)**2)

    beta = num/np.sum(num, axis=1)[:, np.newaxis]
    llh = np.sum(np.log(np.sum(num, axis=1)))

    return beta, llh


def m_step(X, mu, beta, k, N, p, gamma):
    bounds = np.zeros((k, 3))

    # Maximization
    kappa = np.zeros(k)
    pi = np.sum(beta, axis=0)/N
    for j in range(k):  # For each component
        # Compute Sj (4.5)
        Sj = np.einsum('i,ij,ik->jk', beta[:, j], X, X, optimize='greedy') / np.sum(beta[:, j])
        # Compute mu using (4.4) [1], ignoring negative kappa case
        [w, v] = np.linalg.eig(Sj)
        idx = np.argmax(w)
        mu[j, :] = np.real(v[:, idx])
        # Compute Kappa using (4.5) [1]
        r = np.real(w[idx])
        r = 0.99 if r > 0.999 else r
        bounds[j, :] = [lower_bound(1/2, p/2, r), bound(1/2, p/2, r), upper_bound(1/2, p/2, r)]

    # Reguralize
    bounds = bounds + gamma*(bounds.mean(axis=0) - bounds)
    kappa = bounds[:, 1]

    return mu, kappa, pi, bounds


def convergence(llh, iter, maxiter, tol, verbose):
    # Convergece
    if iter > 0:
        print_t('Iteration: {:d}, llh: {:.2g}, relative llh change: {:.2g}'.format(iter+1, llh[iter], (llh[iter] - llh[iter-1])/abs(llh[iter-1])), verbose)
    else:
        print_t('Iteration: {:d}, llh: {:.2g}'.format(iter+1, llh[iter]), verbose)

    if iter > 0 and (llh[iter] - llh[iter-1])/abs(llh[iter-1]) < tol:
        if llh[iter] - llh[iter-1] > 0:
            print_t('Conveged in {} iterations'.format(iter+1), verbose)
            return iter
        else:
            print_t('Conveged in {} iterations (Igoring iteration after maximum was reached)'.format(iter+1), verbose)
            return iter-1
    elif iter >= maxiter-1:
        print_t('Did not converge in maximum iterations')
        return iter

    return -1


# Definition of bounds solutions for Kappa (3.7)(3.8)(3.9) [1]
def lower_bound(a, c, r):
    return (r*c-a)/(r*(1-r))*(1+(1-r)/(c-a))


def bound(a, c, r):
    return (r*c-a)/(2*r*(1-r))*(1+np.sqrt(1+(4*(c+1)*r*(1-r)) / (a*(c-a))))


def upper_bound(a, c, r):
    return (r*c-a)/(r*(1-r))*(1+r/a)


def kummer(a, b, kappa, tol=1e-10, return_iter=False):
    term = a*kappa/b
    f = 1+term
    j = 1
    while abs(term) > tol:
        j += 1
        a += 1
        b += 1
        term *= a*kappa/b/j
        f += term

    if return_iter:
        return f, j
    return f


def print_t(s, verbose=True):
    if verbose:
        print('{:%H:%M:%S}: {}'.format(datetime.datetime.now(), s))
