"""Model zoo. Every model takes a feature DataFrame and optional sample weights."""
from __future__ import annotations

import warnings

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_KINDS = ["surface_mean", "ridge", "knn", "rf", "lgbm", "gp"]
MODEL_LABELS = {
    "surface_mean": "Dry/wet average (baseline)",
    "ridge": "Ridge regression (linear)",
    "knn": "Nearest tested tyres (kNN)",
    "rf": "Random forest",
    "lgbm": "Gradient boosting (LightGBM)",
    "gp": "Gaussian process",
}


class SurfaceMean:
    """Baseline: the (weighted) average of the target per surface."""

    def fit(self, X, y, w=None):
        w = np.ones(len(y)) if w is None else np.asarray(w, float)
        dry = X["IsDry"].to_numpy()
        self.overall = float(np.average(y, weights=w))
        self.by = {v: float(np.average(y[dry == v], weights=w[dry == v])) for v in np.unique(dry)}
        return self

    def predict(self, X):
        return np.array([self.by.get(v, self.overall) for v in X["IsDry"].to_numpy()])


class SkModel:
    """Imputation + scaling + estimator. Weights are passed when supported."""

    def __init__(self, kind, seed=0, **params):
        self.kind, self.seed, self.params = kind, seed, params

    def _estimator(self, n):
        k, p = self.kind, self.params
        if k == "ridge":
            return RidgeCV(alphas=np.logspace(-2, 3, 21))
        if k == "knn":
            return KNeighborsRegressor(n_neighbors=min(p.get("k", 7), max(n - 1, 1)), weights="distance")
        if k == "rf":
            return RandomForestRegressor(n_estimators=p.get("n_estimators", 300), min_samples_leaf=3,
                                         max_features=0.5, n_jobs=p.get("n_jobs", -1),
                                         random_state=self.seed)
        if k == "lgbm":
            import lightgbm as lgb
            return lgb.LGBMRegressor(n_estimators=p.get("n_estimators", 300), learning_rate=0.03,
                                     num_leaves=p.get("num_leaves", 7), min_child_samples=10,
                                     subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                     reg_lambda=1.0, random_state=self.seed, verbose=-1, n_jobs=1)
        if k == "gp":
            kern = ConstantKernel(1.0, (1e-2, 1e2)) * RBF(3.0, (0.3, 100.0)) + WhiteKernel(0.1, (1e-4, 1.0))
            return GaussianProcessRegressor(kern, normalize_y=True, random_state=self.seed)
        raise ValueError(k)

    def fit(self, X, y, w=None):
        self.cols = list(X.columns)
        self.pipe = Pipeline([("imp", SimpleImputer(strategy="median", keep_empty_features=True)),
                              ("sc", StandardScaler()), ("m", self._estimator(len(y)))])
        kw = {}
        if w is not None and self.kind in ("ridge", "rf", "lgbm"):
            kw["m__sample_weight"] = np.asarray(w, float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.pipe.fit(X[self.cols], y, **kw)
        return self

    def predict(self, X):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return self.pipe.predict(X[self.cols])


def make_model(kind, seed=0, **params):
    return SurfaceMean() if kind == "surface_mean" else SkModel(kind, seed, **params)
