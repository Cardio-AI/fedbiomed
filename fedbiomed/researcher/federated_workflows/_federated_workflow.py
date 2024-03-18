# This file is originally part of Fed-BioMed
# SPDX-License-Identifier: Apache-2.0

""" This file defines the FederatedWorkflow class and some additional generic utility functions that can be used by
    all other workflows."""

import functools, json, os, sys, tabulate, traceback, uuid
from abc import ABC, abstractmethod
from copy import deepcopy
from pathvalidate import sanitize_filename
from re import findall
from typing import Any, Dict, List, TypeVar, Union, Optional, Tuple

from fedbiomed.common.constants import ErrorNumbers, JOB_PREFIX, __breakpoints_version__
from fedbiomed.common.exceptions import (
    FedbiomedExperimentError, FedbiomedError, FedbiomedSilentTerminationError, FedbiomedTypeError, FedbiomedValueError
)
from fedbiomed.common.ipython import is_ipython
from fedbiomed.common.logger import logger
from fedbiomed.common.training_args import TrainingArgs
from fedbiomed.common.utils import raise_for_version_compatibility, __default_version__

from fedbiomed.researcher.datasets import FederatedDataSet
from fedbiomed.researcher.environ import environ
from fedbiomed.researcher.filetools import create_exp_folder, find_breakpoint_path, choose_bkpt_file
from fedbiomed.researcher.node_state_agent import NodeStateAgent
from fedbiomed.researcher.requests import Requests
from fedbiomed.researcher.secagg import SecureAggregation

TFederatedWorkflow = TypeVar("TFederatedWorkflow", bound='FederatedWorkflow')  # only for typing


# Exception handling at top level for researcher
def exp_exceptions(function):
    """
    Decorator for handling all exceptions in the Experiment class() :
    pretty print a message for the user, quit Experiment.
    """

    # wrap the original function catching the exceptions
    @functools.wraps(function)
    def payload(*args, **kwargs):
        code = 0
        try:
            ret = function(*args, **kwargs)
        except FedbiomedSilentTerminationError:
            # handle the case of nested calls will exception decorator
            raise
        except SystemExit as e:
            # handle the sys.exit() from other clauses
            sys.exit(e)
        except KeyboardInterrupt:
            code = 1
            print(
                '\n--------------------',
                'Fed-BioMed researcher stopped due to keyboard interrupt',
                '--------------------',
                sep=os.linesep)
            logger.critical('Fed-BioMed researcher stopped due to keyboard interrupt')
        except FedbiomedError as e:
            code = 1
            print(
                '\n--------------------',
                f'Fed-BioMed researcher stopped due to exception:\n{str(e)}',
                '--------------------',
                sep=os.linesep)
            # redundant, should be already logged when raising exception
            # logger.critical(f'Fed-BioMed researcher stopped due to exception:\n{str(e)}')
        except BaseException as e:
            code = 3
            print(
                '\n--------------------',
                f'Fed-BioMed researcher stopped due to unknown error:\n{str(e)}',
                'More details in the backtrace extract below',
                '--------------------',
                sep=os.linesep)
            # at most 5 backtrace entries to avoid too long output
            traceback.print_exc(limit=5, file=sys.stdout)
            print('--------------------')
            logger.critical(f'Fed-BioMed stopped due to unknown error:\n{str(e)}')

        if code != 0:
            if is_ipython():
                # raise a silent specific exception, don't exit the interactive kernel
                raise FedbiomedSilentTerminationError
            else:
                # exit the process
                sys.exit(code)

        return ret

    return payload


class FederatedWorkflow(ABC):
    """
    A FederatedWorkflow is the abstract entry point for the researcher to orchestrate both local and remote operations.

    The FederatedWorkflow is an abstract base class from which the actual classes used by the researcher must inherit.
    It manages the life-cycle of:

    - the training arguments
    - secure aggregation
    - the node state agent

    Additionally, it provides the basis for the breakpoint functionality, and manages some backend functionalities such
    as the temporary directory, the experiment ID, etc...

    The attributes `training_data`, `tags` and `nodes` are co-dependent. Attempting to modify one of those may result
    in side effects modifying the other, according to the following rules:
    - setting any of those to None leaves the others untouched, potentially leaving the class in an inconsistent state
    - modifying tags or nodes when training data is None will simply set the intended value
    - modifying tags if training data is not None will reset the training data based on the
        current nodes and the new tags, and afterwards the nodes will be updated according to the new training data
    - modifying nodes if training data is not None will reset the training data based on the
        current tags and the new nodes, and then the nodes will be updated again according to the new training data
    - modifying the training data from tags will update the nodes, while modifying the training data from an object
        resets the tags to None and updates the nodes

    """

    @exp_exceptions
    def __init__(
            self,
            tags: Union[List[str], str, None] = None,
            nodes: Union[List[str], None] = None,
            training_data: Union[FederatedDataSet, dict, None] = None,
            training_args: Union[TrainingArgs, dict, None] = None,
            experimentation_folder: Union[str, None] = None,
            secagg: Union[bool, SecureAggregation] = False,
            save_breakpoints: bool = False,
    ) -> None:
        """Constructor of the class.

        Args:
            tags: list of string with data tags or string with one data tag. Empty list of tags ([]) means any dataset
                is accepted, it is different from None (tags not set, cannot search for training_data yet).
            nodes: list of node_ids to filter the nodes to be involved in the experiment. Defaults to None (no
                filtering).
            training_data:
                * If it is a FederatedDataSet object, use this value as training_data.
                * else if it is a dict, create and use a FederatedDataSet object from the dict and use this value as
                    training_data. The dict should use node ids as keys, values being list of dicts (each dict
                    representing a dataset on a node).
                * else if it is None (no training data provided)
                  - if `tags` is not None, set training_data by
                    searching for datasets with a query to the nodes using `tags` and `nodes`
                  - if `tags` is None, set training_data to None (no training_data set yet,
                    experiment is not fully initialized and cannot be launched)
                Defaults to None (query nodes for dataset if `tags` is not None, set training_data
                to None else)
            training_args: contains training arguments passed to the `training_routine` of the training plan when
                launching it: lr, epochs, batch_size...
            save_breakpoints: whether to save breakpoints or not after each training round. Breakpoints can be used for
                resuming a crashed experiment.
            experimentation_folder: choose a specific name for the folder where experimentation result files and
                breakpoints are stored. This should just contain the name for the folder not a path. The name is used
                as a subdirectory of `environ[EXPERIMENTS_DIR])`. Defaults to None (auto-choose a folder name)
                - Caveat : if using a specific name this experimentation will not be automatically detected as the last
                experimentation by `load_breakpoint`
                - Caveat : do not use a `experimentation_folder` name finishing with numbers ([0-9]+) as this would
                confuse the last experimentation detection heuristic by `load_breakpoint`.
            secagg: whether to setup a secure aggregation context for this experiment, and use it
                to send encrypted updates from nodes to researcher. Defaults to `False`
        """
        # predefine all class variables, so no need to write try/except
        # block each time we use it
        self._fds: Optional[FederatedDataSet] = None  # dataset metadata from the full federation
        self._reqs: Requests = Requests()
        self._nodes_filter: Optional[List[str]] = None  # researcher-defined nodes filter
        self._training_args: Optional[TrainingArgs] = None
        self._tags: Optional[List[str]] = None
        self._experimentation_folder: Optional[str] = None
        self._secagg: Optional[SecureAggregation] = None
        self._save_breakpoints: Optional[bool] = None
        self._node_state_agent: Optional[NodeStateAgent] = None
        self._researcher_id: str = environ['RESEARCHER_ID']
        self._id: str = JOB_PREFIX + str(uuid.uuid4())  # creating a unique job id # TO BE RENAMED

        # set internal members from constructor arguments
        self.set_secagg(secagg)
        self.set_tags(tags)
        self.set_nodes(nodes)
        self.set_save_breakpoints(save_breakpoints)
        self.set_training_args(training_args)
        # set training data
        if training_data is not None:
            # if training data was provided, it takes precedence
            self.set_training_data(training_data)
        elif self.tags() is not None:
            # if tags were provided, set training data from tags
            self.set_training_data(training_data, True)
        self.set_experimentation_folder(experimentation_folder)
        self._node_state_agent = NodeStateAgent(list(self._fds.data().keys())
                                                if self._fds and self._fds.data() else [])

    @property
    def secagg(self) -> SecureAggregation:
        """Gets secagg object `SecureAggregation`

        Returns:
            Secure aggregation object.
        """
        return self._secagg

    @exp_exceptions
    def tags(self) -> Union[List[str], None]:
        """Retrieves the tags from the experiment object.

        Please see [`set_tags`][fedbiomed.researcher.federated_workflows.FederatedWorkflow.set_tags] to set tags.

        Returns:
            List of tags that has been set. `None` if it isn't declare yet.
        """
        return self._tags

    @exp_exceptions
    def nodes(self) -> Union[List[str], None]:
        """Retrieves the nodes filter for the execution of the workflow.

        If nodes is None, then no filtering is applied, and all the nodes in the federation participate in the
        execution of the workflow.
        If nodes is not None, then the semantics of the nodes filter are as follows:

        | node_id in nodes filter | node_id in training data | outcome |
        | --- | --- | --- |
        | yes | yes | this node is part of the federation, but will not be considered for executing the workflow |
        | yes | no | ignored |
        | no | yes | this node is part of the federation and will take part in the execution the workflow |
        | no | no | ignored |

        Please see [`set_nodes`][fedbiomed.researcher.federated_workflows.FederatedWorkflow.set_nodes] to set `nodes`.

        Returns:
            The list of nodes to keep for workflow execution, or None if no filtering is applied
        """
        return self._nodes_filter

    @exp_exceptions
    def all_federation_nodes(self) -> List[str]:
        """Returns all the node ids in the federation"""
        return list(self._fds.data().keys()) if self._fds is not None else []

    @exp_exceptions
    def filtered_federation_nodes(self) -> List[str]:
        """Returns the node ids in the federation after filtering with the nodes filter"""
        if self._nodes_filter is not None:
            return [node for node in self.all_federation_nodes() if node in self._nodes_filter]
        else:
            return self.all_federation_nodes()

    @exp_exceptions
    def training_data(self) -> Union[FederatedDataSet, None]:
        """Retrieves the training data which is an instance of
        [`FederatedDataset`][fedbiomed.researcher.datasets.FederatedDataSet]

        This represents the dataset metadata available for the full federation.

        Please see [`set_training_data`][fedbiomed.researcher.federated_workflows.FederatedWorkflow.set_training_data]
        to set or update training data.

        Returns:
            Object that contains metadata for the datasets of each node. `None` if it isn't set yet.
        """
        return self._fds

    @exp_exceptions
    def experimentation_folder(self) -> str:
        """Retrieves the folder name where experiment data/result are saved.

        Please see also [`set_experimentation_folder`]
        [fedbiomed.researcher.federated_workflows.FederatedWorkflow.set_experimentation_folder]

        Returns:
            File name where experiment related files are saved
        """

        return self._experimentation_folder

    @exp_exceptions
    def experimentation_path(self) -> str:
        """Retrieves the file path where experimentation folder is located and experiment related files are saved.

        Returns:
            Experiment directory where all experiment related files are saved
        """

        return os.path.join(environ['EXPERIMENTS_DIR'], self._experimentation_folder)

    @exp_exceptions
    def training_args(self) -> dict:
        """Retrieves training arguments.

        Please see also [`set_training_args`][fedbiomed.researcher.federated_workflows.FederatedWorkflow.set_training_args]

        Returns:
            The arguments that are going to be passed to the training plan's `training_routine` to perfom training on
                the node side. An example training routine: [`TorchTrainingPlan.training_routine`]
                [fedbiomed.common.training_plans.TorchTrainingPlan.training_routine]
        """

        return self._training_args.dict()

    @property
    def id(self):
        """Retrieves the unique experiment identifier."""
        return self._id

    @exp_exceptions
    def save_breakpoints(self) -> bool:
        """Retrieves the status of saving breakpoint after each round of training.

        Returns:
            `True`, If saving breakpoint is active. `False`, vice versa.
        """

        return self._save_breakpoints

    @exp_exceptions
    def info(self, info=None) -> Dict[str, Any]:
        """Prints out the information about the current status of the experiment.

        Lists  all the parameters/arguments of the experiment and informs whether the experiment can be run.

        Raises:
            FedbiomedExperimentError: Inconsistent experiment due to missing variables
        """
        if info is None:
            info = {
                'Arguments': [],
                'Values': []
            }
        info['Arguments'].extend([
                'Tags',
                'Nodes filter',
                'Training Data',
                'Training Arguments',
                'Experiment folder',
                'Experiment Path',
                'Secure Aggregation'
            ])
        info['Values'].extend(['\n'.join(findall('.{1,60}', str(e))) for e in [
                           self._tags,
                           self._nodes_filter,
                           self._fds,
                           self._training_args,
                           self._experimentation_folder,
                           self.experimentation_path(),
                           f'- Using: {self._secagg}\n- Active: {self._secagg.active}'
                       ]])
        print(tabulate.tabulate(info, headers='keys'))
        return info

    # Setters
    @exp_exceptions
    def set_tags(self, tags: Union[List[str], str, None]) -> Union[List[str], None]:
        """Sets tags + verifications on argument type

        Ensures consistency with the tags attribute following this definition:
        - if training data is None, then the attributes are consistent
        - if training data is not None and tags is not None, check that the tags from the training data all contain
            the tags declared in the self._tags attribute

        The state diagram below explains the flow of setters and consistency checks
        ![state diagram](https://www.plantuml.com/plantuml/png/ZP91ZzCm48Nl_XLpHjf3usGFqIhQ0ul4XGEik22qCiviQz7OnPvK8CH_9rESAgdjqalhsFE-zsRinq3AqpZinRGWXAUV1_HcG4llhI7uBG2UlJBMsErRHUfbMb4B7vn5Fb7RiDpv8oBb4nAVFRlFQZzYIgbQE7Wy6ZS6E799XF41JVyP4Xka85a2oKoafR84l5ytTv_moy12htKB5BzV-cbZHjStezzvDx0aPJSNRFYc0lRWBBYHj1iGt2ju_35YeDctoVbUNFlTNNTnXwMAT09p4te33mzwvup6hWCXrZm6S4aBUeVwEsXdWmc4Ll_YpCJjAjl3Qn-4td1rSIej67kMKtGFv0uS06tVTP4GDrjOL9_JoZHjqbjCBMzABKfvcV5HcO1FtZlFyQFIXDFRMtHGdpjO2EPEwkiMMWgXvVeg6L-ULpMxHLtSpCvh-lMqWKbnMasQk9Cy7JOS0thuDzqLe4e0LHBucbucUfbxAbbEleRLNzvyNRdKYKkTS_b_kqq2Qgw_x3L9Y4Uq_JZi_m80)

        This function has the following behaviour:

        - if input is None, then it sets tags attribute to None and immediately exits
        - in input is the same as current tags, then immediately exits
        - otherwise, sets the tags to the inputted value
        - if new tags are inconsistent with training data, it resets the training data attributes

        !!! warning "Setting to None forfeits consistency checks"
            Setting tags to None does not trigger consistency checks, and may therefore leave the class in an
            inconsistent state.

        Args:
            tags: List of string with data tags or string with one data tag. Empty list
                of tags ([]) means any dataset is accepted, it is different from None (tags not set, cannot search
                for training_data yet).

        Returns:
            List of tags that are set. None, if the argument `tags` is None.

        Raises:
            FedbiomedTypeError : Bad tags type
            FedbiomedValueError : Some issue prevented resetting the training data after an inconsistency was detected
        """
        # preprocess the tags argument to correct typing
        if isinstance(tags, list):
            if not all(map(lambda tag: isinstance(tag, str), tags)):
                msg = ErrorNumbers.FB410.value + f' `tags` must be a str, a list of str, or None'
                logger.critical(msg)
                raise FedbiomedTypeError(msg)
            tags_to_set = tags
        elif isinstance(tags, str):
            tags_to_set = [tags]
        elif tags is None:
            # when setting to None, exit immediately after
            self._tags = None
            return self._tags
        else:
            msg = ErrorNumbers.FB410.value + f' `tags` must be a str, a list of str, or None'
            logger.critical(msg)
            raise FedbiomedTypeError(msg)
        # do nothing if attempting to set the same value as current tags to avoid redundant network calls
        if self._tags is not None and set(tags_to_set) == set(self._tags):
            return self._tags
        # set the tags
        self._tags = tags_to_set
        # check for consistency
        if not self._fds_tags_consistent():
            # if inconsistent, reset the training data
            try:
                self.set_training_data(None, from_tags=True)
            except FedbiomedError as e:
                msg = f"{ErrorNumbers.FB410.value} in `set_tags`. Automatic attempt to fix inconsistency between " \
                      f"tags and training data failed. Please reset tags and training data to None before " \
                      f"attempting to modify them again."
                logger.critical(msg)
                raise FedbiomedValueError(msg) from e
        return self._tags

    @exp_exceptions
    def set_nodes(self, nodes: Union[List[str], None]) -> Union[List[str], None]:
        """Sets the nodes filter + verifications on argument type

        Args:
            nodes: List of node_ids to filter the nodes to be involved in the experiment.

        Returns:
            List of nodes that are set. None, if the argument `nodes` is None.

        Raises:
            FedbiomedTypeError : Bad nodes type
        """
        # immediately exit if setting nodes to None
        if nodes is None:
            self._nodes_filter = None
        # set nodes
        elif isinstance(nodes, list):
            if not all(map(lambda node: isinstance(node, str), nodes)):
                msg = ErrorNumbers.FB410.value + f' `nodes` argument must be a list of strings or None.'
                logger.critical(msg)
                raise FedbiomedTypeError(msg)
            self._nodes_filter = nodes
        else:
            msg = ErrorNumbers.FB410.value + f' `nodes` argument must be a list of strings or None.'
            logger.critical(msg)
            raise FedbiomedTypeError(msg)
        return self._nodes_filter

    @exp_exceptions
    def set_training_data(
            self,
            training_data: Union[FederatedDataSet, dict, None],
            from_tags: bool = False) -> \
            Union[FederatedDataSet, None]:
        """Sets training data for federated training + verification on arguments type

        Ensures consistency with the tags attribute following this definition:
        - if training data is None, then the attributes are consistent
        - if training data is not None and tags is not None, check that the tags from the training data all contain
            the tags declared in the self._tags attribute

        The state diagram below explains the flow of setters and consistency checks
        ![state diagram](https://www.plantuml.com/plantuml/png/ZP91ZzCm48Nl_XLpHjf3usGFqIhQ0ul4XGEik22qCiviQz7OnPvK8CH_9rESAgdjqalhsFE-zsRinq3AqpZinRGWXAUV1_HcG4llhI7uBG2UlJBMsErRHUfbMb4B7vn5Fb7RiDpv8oBb4nAVFRlFQZzYIgbQE7Wy6ZS6E799XF41JVyP4Xka85a2oKoafR84l5ytTv_moy12htKB5BzV-cbZHjStezzvDx0aPJSNRFYc0lRWBBYHj1iGt2ju_35YeDctoVbUNFlTNNTnXwMAT09p4te33mzwvup6hWCXrZm6S4aBUeVwEsXdWmc4Ll_YpCJjAjl3Qn-4td1rSIej67kMKtGFv0uS06tVTP4GDrjOL9_JoZHjqbjCBMzABKfvcV5HcO1FtZlFyQFIXDFRMtHGdpjO2EPEwkiMMWgXvVeg6L-ULpMxHLtSpCvh-lMqWKbnMasQk9Cy7JOS0thuDzqLe4e0LHBucbucUfbxAbbEleRLNzvyNRdKYKkTS_b_kqq2Qgw_x3L9Y4Uq_JZi_m80)

        The full expected behaviour when changing training data is given in the table below:
        | New value of `training_data` | `from_tags` | Consistency with tags | Outcome |
        | --- | --- | --- | --- |
        | dict or FederatedDataset | True or False | consistent with new value | only set fds attribute |
        | dict or FederatedDataset | True | any | fail because user is attempting to set from tags but also providing a training_data argument|
        | dict or FederatedDataset | False | tags and nodes are not consistent with new value | set fds attribute, set tags to None |
        | None | True | any | fail if tags is None, else set fds attribute based tags |
        | None | False | any | set tags to None and keep same value and tags |

        !!! warning "Setting to None forfeits consistency checks"
            Setting training_data to None does not trigger consistency checks, and may therefore leave the class in an
            inconsistent state.

        Args:
            training_data:
                * If it is a FederatedDataSet object, use this value as training_data.
                * else if it is a dict, create and use a FederatedDataSet object from the dict
                  and use this value as training_data. The dict should use node ids as keys,
                  values being list of dicts (each dict representing a dataset on a node).
                * else if it is None (no training data provided)
                  - if `from_tags` is True and `tags` is not None, set training_data by
                    searching for datasets with a query to the nodes using `tags` and `nodes`
                  - if `from_tags` is False or `tags` is None, set training_data to None (no training_data set yet,
                    experiment is not fully initialized and cannot be launched)
            from_tags: If True, query nodes for datasets when no `training_data` is provided.
                Not used when `training_data` is provided.

        Returns:
            FederatedDataSet metadata

        Raises:
            FedbiomedTypeError : bad training_data type
            FedbiomedValueError : attempting to set training data from tags but self._tags is None
        """
        if not isinstance(from_tags, bool):
            msg = ErrorNumbers.FB410.value + f' `from_tags` : got {type(from_tags)} but expected a boolean'
            logger.critical(msg)
            raise FedbiomedTypeError(msg)
        if from_tags and training_data is not None:
            msg = ErrorNumbers.FB410.value + f' set_training_data: cannot specify a training_data argument if ' \
                                             f'from_tags is True'
            logger.critical(msg)
            raise FedbiomedValueError(msg)
        # case where no training data are passed
        if training_data is None:
            if from_tags is True:
                if self._tags is None:
                    msg = f"{ErrorNumbers.FB410.value}: attempting to set training data from undefined tags"
                    logger.critical(msg)
                    raise FedbiomedValueError(msg)
                training_data = self._reqs.search(self._tags, self._nodes_filter)
            else:
                self._fds = None
                return None  # quick exit
        # from here, training_data is not None
        if isinstance(training_data, FederatedDataSet):
            self._fds = training_data
        elif isinstance(training_data, dict):
            self._fds = FederatedDataSet(training_data)
        else:
            msg = ErrorNumbers.FB410.value + f' `training_data` has incorrect type: {type(training_data)}'
            logger.critical(msg)
            raise FedbiomedTypeError(msg)
        # check and ensure consistency
        if not self._fds_tags_consistent():
            self._tags = self._tags if from_tags else None  # reset tags to None unless we used them to fetch the data
        # return the new value
        return self._fds

    @exp_exceptions
    def set_experimentation_folder(self, experimentation_folder: Optional[str] = None) -> str:
        """Sets `experimentation_folder`, the folder name where experiment data/result are saved.

        Args:
            experimentation_folder: File name where experiment related files are saved

        Returns:
            experimentation_folder (str)

        Raises:
            FedbiomedExperimentError : bad `experimentation_folder` type
        """
        if experimentation_folder is None:
            self._experimentation_folder = create_exp_folder()
        elif isinstance(experimentation_folder, str):
            sanitized_folder = sanitize_filename(experimentation_folder, platform='auto')
            self._experimentation_folder = create_exp_folder(sanitized_folder)
            if sanitized_folder != experimentation_folder:
                logger.warning(f'`experimentation_folder` was sanitized from '
                               f'{experimentation_folder} to {sanitized_folder}')
        else:
            msg = ErrorNumbers.FB410.value + \
                  f' `experimentation_folder` : {type(experimentation_folder)}'
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

            # at this point self._experimentation_folder is a str valid for a foldername

        return self._experimentation_folder

    @exp_exceptions
    def set_training_args(self, training_args: Union[dict, TrainingArgs, None]) -> Union[dict, None]:
        """ Sets `training_args` + verification on arguments type

        Args:
            training_args (dict): contains training arguments passed to the training plan's `training_routine` such as
                lr, epochs, batch_size...

        Returns:
            Training arguments

        Raises:
            FedbiomedExperimentError : bad training_args type
        """

        if isinstance(training_args, TrainingArgs):
            self._training_args = deepcopy(training_args)
        elif isinstance(training_args, dict) or training_args is None:
            self._training_args = TrainingArgs(training_args, only_required=False)
        else:
            msg = f"{ErrorNumbers.FB410.value} in function `set_training_args`. Expected type TrainingArgs, dict, or " \
                  f"None, got {type(training_args)} instead."
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

        # Propagate training arguments to job
        return self._training_args.dict()

    @exp_exceptions
    def set_secagg(self, secagg: Union[bool, SecureAggregation]):

        if isinstance(secagg, bool):
            self._secagg = SecureAggregation(active=secagg)
        elif isinstance(secagg, SecureAggregation):
            self._secagg = secagg
        else:
            msg = f"{ErrorNumbers.FB410.value}: Expected `secagg` argument bool or `SecureAggregation`, " \
                  f"but got {type(secagg)}"
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

        return self._secagg

    @exp_exceptions
    def set_save_breakpoints(self, save_breakpoints: bool) -> bool:
        """ Setter for save_breakpoints + verification on arguments type

        Args:
            save_breakpoints (bool): whether to save breakpoints or
                not after each training round. Breakpoints can be used for resuming
                a crashed experiment.

        Returns:
            Status of saving breakpoints

        Raises:
            FedbiomedExperimentError: bad save_breakpoints type
        """
        if isinstance(save_breakpoints, bool):
            self._save_breakpoints = save_breakpoints
            # no warning if done during experiment, we may change breakpoint policy at any time
        else:
            msg = ErrorNumbers.FB410.value + f' `save_breakpoints` : {type(save_breakpoints)}'
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

        return self._save_breakpoints

    def secagg_setup(self, sampled_nodes: List[str]) -> Dict:
        """Retrieves the secagg arguments for setup."""
        secagg_arguments = {}
        if self._secagg.active:
            self._secagg.setup(parties=[environ["ID"]] + sampled_nodes,
                               job_id=self._id)
            secagg_arguments = self._secagg.train_arguments()
        return secagg_arguments

    def _update_nodes_states_agent(self, before_training: bool = True):
        """Updates [`NodeStateAgent`][fedbiomed.researcher.node_state_agent.NodeStateAgent], with the latest
        state_id coming from `Nodes` contained among all `Nodes` within
        [`FederatedDataset`][fedbiomed.researcher.datasets.FederatedDataSet].

        Args:
            before_training: whether to update `NodeStateAgent` at the begining or at the end of a `Round`:
                - if before, only updates `NodeStateAgent` wrt `FederatedDataset`, otherwise
                - if after, updates `NodeStateAgent` wrt the latest reply
        """
        node_ids = list(self._fds.data().keys()) if self._fds and self._fds.data() else []
        self._node_state_agent.update_node_states(node_ids)

    @exp_exceptions
    def breakpoint(self,
                   state: Dict,
                   bkpt_number: int) -> None:
        """
        Saves breakpoint with the state of the workflow.

        The following attributes will be saved:

          - tags
          - experimentation_folder
          - training_data
          - training_args
          - secagg
          - node_state

        Raises:
            FedbiomedExperimentError: experiment not fully defined, experiment did not run any round yet, or error when
                saving breakpoint
        """
        state.update({
            'id': self._id,
            'breakpoint_version': str(__breakpoints_version__),
            'training_data': self._fds.data(),
            'training_args': self._training_args.dict(),
            'experimentation_folder': self._experimentation_folder,
            'tags': self._tags,
            'nodes': self._nodes_filter,
            'secagg': self._secagg.save_state_breakpoint(),
            'node_state':  self._node_state_agent.save_state_breakpoint()
        })

        # save state into a json file
        breakpoint_path, breakpoint_file_name = \
            choose_bkpt_file(self._experimentation_folder, bkpt_number - 1)
        breakpoint_file_path = os.path.join(breakpoint_path, breakpoint_file_name)
        try:
            with open(breakpoint_file_path, 'w') as bkpt:
                json.dump(state, bkpt)
            logger.info(f"breakpoint number {bkpt_number - 1} saved at " +
                        os.path.dirname(breakpoint_file_path))
        except (OSError, PermissionError, ValueError, TypeError, RecursionError) as e:
            # - OSError: heuristic for catching open() and write() errors
            # - see json.dump() documentation for documented errors for this call
            msg = ErrorNumbers.FB413.value + f' - save failed with message {str(e)}'
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

    @classmethod
    @exp_exceptions
    def load_breakpoint(cls,
                        breakpoint_folder_path: Optional[str] = None) -> Tuple[TFederatedWorkflow, dict]:
        """
        Loads breakpoint (provided a breakpoint has been saved)
        so the workflow can be resumed.

        Args:
          breakpoint_folder_path: path of the breakpoint folder. Path can be absolute or relative eg:
            "var/experiments/Experiment_xxxx/breakpoints_xxxx". If None, loads the latest breakpoint of the latest
            workflow. Defaults to None.

        Returns:
            Reinitialized workflow object.

        Raises:
            FedbiomedExperimentError: bad argument type, error when reading breakpoint or bad loaded breakpoint
                content (corrupted)
        """
        # check parameters type
        if not isinstance(breakpoint_folder_path, str) and breakpoint_folder_path is not None:
            msg = (
                f"{ErrorNumbers.FB413.value}: load failed, `breakpoint_folder_path`"
                f" has bad type {type(breakpoint_folder_path)}"
            )
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

        # get breakpoint folder path (if it is None) and state file
        breakpoint_folder_path, state_file = find_breakpoint_path(breakpoint_folder_path)
        breakpoint_folder_path = os.path.abspath(breakpoint_folder_path)

        try:
            path = os.path.join(breakpoint_folder_path, state_file)
            with open(path, "r", encoding="utf-8") as file:
                saved_state = json.load(file)
        except (json.JSONDecodeError, OSError) as exc:
            # OSError: heuristic for catching file access issues
            msg = (
                f"{ErrorNumbers.FB413.value}: load failed,"
                f" reading breakpoint file failed with message {exc}"
            )
            logger.critical(msg)
            raise FedbiomedExperimentError(msg) from exc
        if not isinstance(saved_state, dict):
            msg = (
                f"{ErrorNumbers.FB413.value}: load failed, breakpoint file seems"
                f" corrupted. Type should be `dict` not {type(saved_state)}"
            )
            logger.critical(msg)
            raise FedbiomedExperimentError(msg)

        # First, check version of breakpoints
        bkpt_version = saved_state.get('breakpoint_version', __default_version__)
        raise_for_version_compatibility(bkpt_version, __breakpoints_version__,
                                        f"{ErrorNumbers.FB413.value}: Breakpoint file was generated with version %s "
                                        f"which is incompatible with the current version %s.")

        # retrieve breakpoint training data
        bkpt_fds = saved_state.get('training_data')
        bkpt_fds = FederatedDataSet(bkpt_fds)

        # initializing experiment
        loaded_exp = cls()
        loaded_exp._id = saved_state.get('id')
        loaded_exp.set_training_data(bkpt_fds)
        loaded_exp.set_tags(saved_state.get('tags'))
        loaded_exp.set_nodes(saved_state.get('nodes'))
        loaded_exp.set_training_args(saved_state.get('training_args'))
        loaded_exp.set_experimentation_folder(saved_state.get('experimentation_folder'))
        loaded_exp.set_secagg(SecureAggregation.load_state_breakpoint(saved_state.get('secagg')))
        loaded_exp._node_state_agent.load_state_breakpoint(saved_state.get('node_state'))
        loaded_exp.set_save_breakpoints(True)

        return loaded_exp, saved_state

    def _fds_tags_consistent(self) -> bool:
        """Checks whether the tags and training data are consistent.

        Consistency is defined as follows:
        - if training data is None, then the attributes are consistent
        - if training data is not None and tags is not None, check that the tags from the training data all contain
            the tags declared in the self._tags attribute

        Returns:
            Bool indicating whether the three attributes are considered consistent
        """
        if self.training_data() is not None and self.tags() is not None:
            if len(self.training_data().data()) == 0:
                return False
            tags_from_training_data = [x['tags'] for x in self.training_data().data().values()]
            if not all(all(t in x for t in self.tags()) for x in tags_from_training_data):
                return False
        return True

    @abstractmethod
    def run(self) -> int:
        """Run the experiment"""
