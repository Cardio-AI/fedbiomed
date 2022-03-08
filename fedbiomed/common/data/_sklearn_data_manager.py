import numpy as np
import pandas as pd
from typing import Union, Tuple

from numpy import ndarray
from pandas import DataFrame, Series
from sklearn.model_selection import train_test_split
from fedbiomed.common.exceptions import FedbiomedSkLearnDataManagerError
from fedbiomed.common.constants import ErrorNumbers


class SkLearnDataManager(object):

    def __init__(self,
                 inputs: Union[np.ndarray, pd.DataFrame, pd.Series],
                 target: Union[np.ndarray, pd.DataFrame, pd.Series],
                 **kwargs):

        """
        Wrapper for `pd.DataFrame`, `pd.Series` and `np.ndarray` datasets that is going to  be
        used for scikit-learn based model training. This class is responsible for managing inputs, and
        target variables that have been provided in `training_data` of scikit-learn based training
        plans.

        Args:
            inputs (np.ndarray, pd.DataFrame, pd.Series): Independent variables (inputs, features) for model training
            target (np.ndarray, pd.DataFrame, pd.Series): Dependent variable/s (target) for model training and
                                                            evaluation

        Attr:
            _loader_arguments: The arguments that are going to be passed to torch.utils.data.DataLoader
            _subset_test: Test subset of dataset
            _subset_train: Train subset of dataset

        Raises:
            none
        """

        # Convert pd.DataFrame or pd.Series to np.ndarray for `inputs`
        if isinstance(inputs, (pd.DataFrame, pd.Series)):
            self._inputs = inputs.to_numpy()
        else:
            self._inputs = inputs

        # Convert pd.DataFrame or pd.Series to np.ndarray for `target`
        if isinstance(target, (pd.DataFrame, pd.Series)):
            self._target = target.to_numpy()
        else:
            self._target = target

        # Additional loader arguments
        self._loader_arguments = kwargs

        # Subset None means that train/test split has not been performed
        self._subset_test: Union[Tuple[np.ndarray, np.ndarray], None] = None
        self._subset_train: Union[Tuple[np.ndarray, np.ndarray], None] = None

    def dataset(self) -> Tuple[Union[ndarray, DataFrame, Series],
                               Union[ndarray, DataFrame, Series]]:
        """
        Getter for dataset. This returns whole dataset as it is without any split.

        Returns:
             Tuple[Union[ndarray, DataFrame, Series], Union[ndarray, DataFrame, Series]]
        """

        # TODO: When a proper DataLoader is develop for SkLearn framework, this method should
        # return pure data not data loader.  The method load_all_samples() should return dataloader
        # please see the method load_all_samples()

        return self._inputs, self._target

    def subset_test(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Getter for Subset of dataset for test partition.

        Raises:
            none

        Returns:
            torch.utils.data.Subset | None
        """

        return self._subset_test

    def subset_train(self) -> Tuple[np.ndarray, np.ndarray]:

        """
        Getter for Subset for train partition.

        Raises:
            none

        Returns:
            torch.utils.data.Subset | None
        """

        return self._subset_train

    def load_test_partition(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Method for loading testing partition of Dataset as pytorch DataLoader. Before calling
        this method Dataset should be split into test and train subset in advance

        Raises:
            FedbiomedError: If Dataset is not split into test and train in advance
        """
        if self._subset_test is None:
            raise FedbiomedSkLearnDataManagerError(
                f"{ErrorNumbers.FB609.value}: Can not find subset for test partition. "
                f"Please make sure that the method `.split(ratio=ration)` DataManager "
                f"object has been called before. ")

        # Empty test set
        if len(self._subset_test) <= 0:
            return None

        # TODO: Create DataLoader for SkLearnDataset to apply batch training
        return self._subset_test

    def load_train_partition(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Method for loading training partition of Dataset as SkLearnDataset. Before calling
        this method Dataset should be split into test and train subset in advance

        Raises:
            FedbiomedError: If Dataset is not split into test and train in advance
        """
        if self._subset_train is None:
            raise FedbiomedSkLearnDataManagerError(
                f"{ErrorNumbers.FB609.value}: Can not find subset for train partition. "
                f"Please make sure that the method `.split(ratio=ration)` DataManager "
                f"object has been called before. ")

        # Empty train set
        if len(self._subset_train) <= 0:
            return None

        # TODO: Create DataLoader for SkLearnDataset to apply batch training
        return self._subset_train

    def load_all_samples(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Method for loading all samples as Numpy ndarray without splitting
        """

        return self._inputs, self._target

    def split(self, ratio: float) -> None:
        """
        Method for splitting np.ndarray dataset into train and test.

        Args:
             ratio (float): Split ratio for testing set ratio. Rest of the samples
                            will be used for training
        Raises:
            FedbiomedSkLearnDataManagerError: If the ratio is not in good format

        Returns:
             none
        """

        # Check the argument `ratio` is of type `float`
        if not isinstance(ratio, (float, int)):
            raise FedbiomedSkLearnDataManagerError(f'{ErrorNumbers.FB608.value}: The argument `ratio` should be '
                                                   f'type `float` or `int` not {type(ratio)}')

        # Check ratio is valid for splitting
        if ratio < 0 or ratio > 1:
            raise FedbiomedSkLearnDataManagerError(f'{ErrorNumbers.FB609.value}: The argument `ratio` should be '
                                                   f'equal or between 0 and 1, not {ratio}')
        if ratio == 0:
            self._subset_train = (self._inputs, self._target)
            self._subset_test = []
        elif ratio == 1:
            self._subset_train = []
            self._subset_test = (self._inputs, self._target)
        else:
            x_train, x_test, y_train, y_test = train_test_split(self._inputs, self._target, test_size=ratio)
            self._subset_test = (x_test, y_test)
            self._subset_train = (x_train, y_train)

        return None
