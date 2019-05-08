import copy as cp

from skmultiflow.core.base import StreamModel
from skmultiflow.drift_detection import ADWIN
from skmultiflow.lazy import KNNAdwin
from skmultiflow.utils import check_random_state
from skmultiflow.utils.utils import *


class OnlineCSB2(StreamModel):
    """ Online CSB2

    Online CSB2 [1]_ is the online version of the ensemble learner CSB2.

    CSB2 algorithm is a compromise between AdaBoost and AdaC2. For correctly
    classified examples, CSB2 treats them in the same way as AdaBoost, while
    for missclassified examples, it does the same as AdaC2. In addition, the
    voting weight of each base learner in CSB2 is the same as AdaBoost.

    This online ensemble learner method is improved by the addition of an ADWIN change
    detector.

    ADWIN stands for Adaptive Windowing. It works by keeping updated
    statistics of a variable sized window, so it can detect changes and
    perform cuts in its window to better adapt the learning algorithms.


    Parameters
    ----------
    base_estimator: StreamModel
        This is the ensemble classifier type, each ensemble classifier is going
        to be a copy of the base_estimator.

    n_estimators: int
        The size of the ensemble, in other words, how many classifiers to train.

    cost_positive: float
        The cost of misclassifying a positive sample.

    cost_negative: float
        The cost of misclassifying a negative sample.

    drift_detection: Bool
        A drift detector (ADWIN) can be used by the method to track the performance
         of the classifiers and adapt when a drift is detected.

    random_state: int, RandomState instance or None, optional (default=None)
        If int, random_state is the seed used by the random number generator;
        If RandomState instance, random_state is the random number generator;
        If None, the random number generator is the RandomState instance used by `np.random`.

    Raises
    ------
    NotImplementedError: A few of the functions described here are not
    implemented since they have no application in this context.

    ValueError: A ValueError is raised if the 'classes' parameter is
    not passed in the first partial_fit call.

    References
    ----------
    .. [1] B. Wang and J. Pineau, "Online Bagging and Boosting for Imbalanced Data Streams,"
           in IEEE Transactions on Knowledge and Data Engineering, vol. 28, no. 12, pp.
           3353-3366, 1 Dec. 2016. doi: 10.1109/TKDE.2016.2609424

    """

    def __init__(self,
                 base_estimator=KNNAdwin(),
                 n_estimators=10,
                 cost_positive=1,
                 cost_negative=0.1,
                 drift_detection=True,
                 random_state=None):
        super().__init__()
        # default values
        self.ensemble = None
        self.n_estimators = None
        self.classes = None
        self.random_state = None
        self._init_n_estimators = n_estimators
        self._init_random_state = random_state
        self.cost_positive = cost_positive
        self.cost_negative = cost_negative
        self.drift_detection = drift_detection
        self.adwin_ensemble = None
        self.lam_fn = None
        self.lam_fp = None
        self.lam_sum = None
        self.lam_sw = None
        self.werr = None
        self.epsilon = None
        self.__configure(base_estimator)

    def __configure(self, base_estimator):
        base_estimator.reset()
        self.base_estimator = base_estimator
        self.n_estimators = self._init_n_estimators
        self.adwin_ensemble = []
        for i in range(self.n_estimators):
            self.adwin_ensemble.append(ADWIN())
        self.ensemble = [cp.deepcopy(base_estimator) for _ in range(self.n_estimators)]
        self.random_state = check_random_state(self._init_random_state)
        self.lam_fn = np.zeros(self.n_estimators)
        self.lam_fp = np.zeros(self.n_estimators)
        self.lam_sum = np.zeros(self.n_estimators)
        self.werr = np.zeros(self.n_estimators)
        self.lam_sw = np.zeros(self.n_estimators)
        self.epsilon = np.zeros(self.n_estimators)

    def reset(self):
        self.__configure(self.base_estimator)

    def fit(self, X, y, classes=None, weight=None):
        self.partial_fit(X, y, classes, weight)

    def partial_fit(self, X, y, classes=None, weight=None):
        """ partial_fit

        Partially fits the model, based on the X and y matrix.

        Since it's an ensemble learner, if X and y matrix of more than one
        sample are passed, the algorithm will partial fit the model one sample
        at a time.

        Each sample is trained by each classifier a total of K times, where K
        is drawn by a Poisson(l) distribution. l is updated after every example
        using :math:`lambda_{sc}` if th estimator correctly classifies the example or
        :math:`lambda_{sw}` in the other case.

        Parameters
        ----------
        X: Numpy.ndarray of shape (n_samples, n_features)
            Features matrix used for partially updating the model.

        y: Array-like
            An array-like of all the class labels for the samples in X.

        classes: list
            List of all existing classes. This is an optional parameter, except
            for the first partial_fit call, when it becomes obligatory.

        weight: Array-like
            Instance weight. If not provided, uniform weights are assumed.

        Raises
        ------
        ValueError: A ValueError is raised if the 'classes' parameter is not
        passed in the first partial_fit call, or if they are passed in further
        calls but differ from the initial classes list passed.
        """
        if self.classes is None:
            if classes is None:
                raise ValueError("The first partial_fit call should pass all the classes.")
            else:
                self.classes = classes

        if self.classes is not None and classes is not None:
            if set(self.classes) == set(classes):
                pass
            else:
                raise ValueError("The classes passed to the partial_fit function differ from those passed earlier.")

        self.__adjust_ensemble_size()
        r, _ = get_dimensions(X)
        for j in range(r):
            change_detected = False
            lam = 1
            for i in range(self.n_estimators):
                self.lam_sum[i] += lam
                k = self.random_state.poisson(lam)
                if k > 0:
                    for b in range(k):
                        self.ensemble[i].partial_fit([X[j]], [y[j]], classes, weight)
                    if self.ensemble[i].predict([X[j]])[0] == y[j]:
                        self.epsilon[i] = self.lam_sw[i] / self.lam_sum[i]
                        self.werr[i] = (self.lam_fp[i] + self.lam_fn[i]) / self.lam_sum[i]
                        if self.epsilon[i] + self.werr[i] != 0 and self.epsilon[i] != 1:
                            lam = self.epsilon[i] / ((1 - self.epsilon[i]) * (self.epsilon[i] + self.werr[i]))
                    else:
                        if self.ensemble[i].predict([X[j]])[0] == 0 and y[j] == 1:
                            self.lam_fp[i] += self.cost_positive * lam
                            self.lam_sw[i] += lam
                            self.epsilon[i] = self.lam_sw[i] / self.lam_sum[i]
                            self.werr[i] = (self.lam_fp[i] + self.lam_fn[i]) / self.lam_sum[i]
                            lam = (self.cost_positive * lam) / (self.epsilon[i] + self.werr[i])
                        else:
                            self.lam_fn[i] += self.cost_positive * lam
                            self.lam_sw[i] += lam
                            self.epsilon[i] = self.lam_sw[i] / self.lam_sum[i]
                            self.werr[i] = (self.lam_fp[i] + self.lam_fn[i]) / self.lam_sum[i]
                            lam = (self.cost_negative * lam) / (self.epsilon[i] + self.werr[i])

                if self.drift_detection:
                    try:
                        pred = self.ensemble[i].predict(X)
                        error_estimation = self.adwin_ensemble[i].estimation
                        for j in range(r):
                            if pred[j] is not None:
                                if pred[j] == y[j]:
                                    self.adwin_ensemble[i].add_element(1)
                                else:
                                    self.adwin_ensemble[i].add_element(0)
                        if self.adwin_ensemble[i].detected_change():
                            if self.adwin_ensemble[i].estimation > error_estimation:
                                change_detected = True
                    except ValueError:
                        change_detected = False
                        pass

            if change_detected and self.drift_detection:
                max_threshold = 0.0
                i_max = -1
                for i in range(self.n_estimators):
                    if max_threshold < self.adwin_ensemble[i].estimation:
                        max_threshold = self.adwin_ensemble[i].estimation
                        i_max = i
                if i_max != -1:
                    self.ensemble[i_max].reset()
                    self.adwin_ensemble[i_max] = ADWIN()

    def __adjust_ensemble_size(self):
        if len(self.classes) != len(self.ensemble):
            if len(self.classes) > len(self.ensemble):
                for i in range(len(self.ensemble), len(self.classes)):
                    self.ensemble.append(cp.deepcopy(self.base_estimator))
                    self.n_estimators += 1
                    self.adwin_ensemble.append(ADWIN())
                self.lam_fn = np.zeros(self.n_estimators)
                self.lam_fp = np.zeros(self.n_estimators)
                self.lam_sum = np.zeros(self.n_estimators)
                self.lam_sw = np.zeros(self.n_estimators)
                self.epsilon = np.zeros(self.n_estimators)
                self.werr = np.zeros(self.n_estimators)

    def predict(self, X):
        """ predict

        The predict function will average the predictions from all its learners
        to find the most likely prediction for the sample matrix X.

        Parameters
        ----------
        X: Numpy.ndarray of shape (n_samples, n_features)
            A matrix of the samples we want to predict.

        Returns
        -------
        numpy.ndarray
            A numpy.ndarray with the label prediction for all the samples in X.

        """
        r, c = get_dimensions(X)
        proba = self.predict_proba(X)
        predictions = []
        if proba is None:
            return None
        for i in range(r):
            predictions.append(np.argmax(proba[i]))
        return np.asarray(predictions)

    def predict_proba(self, X):
        """ predict_proba

        Predicts the probability of each sample belonging to each one of the
        known classes.

        Parameters
        ----------
        X: Numpy.ndarray of shape (n_samples, n_features)
            A matrix of the samples we want to predict.

        Raises
        ------
        ValueError: A ValueError is raised if the number of classes in the base_estimator
        learner differs from that of the ensemble learner.

        Returns
        -------
        numpy.ndarray
            An array of shape (n_samples, n_features), in which each outer entry is
            associated with the X entry of the same index. And where the list in
            index [i] contains len(self.target_values) elements, each of which represents
            the probability that the i-th sample of X belongs to a certain label.

        """
        proba = []
        r, c = get_dimensions(X)
        try:
            for i in range(self.n_estimators):
                partial_proba = self.ensemble[i].predict_proba(X)
                if len(partial_proba[0]) > max(self.classes) + 1:
                    raise ValueError("The number of classes in the base learner is larger than in the ensemble.")

                if len(proba) < 1:
                    for n in range(r):
                        proba.append([0.0 for _ in partial_proba[n]])

                for n in range(r):
                    for l in range(len(partial_proba[n])):
                        try:
                            proba[n][l] += np.log((1 - self.epsilon[i]) / self.epsilon[i]) * partial_proba[n][l]
                        except IndexError:
                            proba[n].append(partial_proba[n][l])
        except ValueError:
            return np.zeros((r, 1))
        except TypeError:
            return np.zeros((r, 1))

        # normalizing probabilities
        sum_proba = []
        for l in range(r):
            sum_proba.append(np.sum(proba[l]))
        aux = []
        for i in range(len(proba)):
            if sum_proba[i] > 0.:
                aux.append([x / sum_proba[i] for x in proba[i]])
            else:
                aux.append(proba[i])
        return np.asarray(aux)

    def score(self, X, y):
        raise NotImplementedError

    def get_info(self):
        return 'OnlineAdaC2 Classifier: base_estimator: ' + str(self.base_estimator) + \
               ' - n_estimators: ' + str(self.n_estimators) + ' - cost positive: ' + str(self.cost_negative) + \
               ' - cost negative: ' + str(self.cost_negative)
