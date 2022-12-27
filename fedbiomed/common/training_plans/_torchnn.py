# This file is originally part of Fed-BioMed
# SPDX-License-Identifier: Apache-2.0

"""TrainingPlan definition for the pytorch deep learning framework."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple, Optional, OrderedDict, Union, Iterator

from copy import deepcopy

import numpy as np
from fedbiomed.common.training_args import TrainingArgs
import torch
from torch import nn

from fedbiomed.common.constants import ErrorNumbers, TrainingPlans
from fedbiomed.common.exceptions import FedbiomedTrainingPlanError
from fedbiomed.common.logger import logger
from fedbiomed.common.metrics import MetricTypes

from fedbiomed.common.privacy import DPController
from fedbiomed.common.utils import get_method_spec

from ._base_training_plan import BaseTrainingPlan

ModelInputType = Union[torch.Tensor, Dict, List, Tuple]


class TorchTrainingPlan(BaseTrainingPlan, ABC):
    """Implements  TrainingPlan for torch NN framework

    An abstraction over pytorch module to run pytorch models and scripts on node side. Researcher model (resp. params)
    will be:

    1. saved  on a '*.py' (resp. '*.pt') files,
    2. uploaded on a HTTP server (network layer),
    3. then Downloaded from the HTTP server on node side,
    4. finally, read and executed on node side.


    Researcher must define/override:
    - a `training_data()` function
    - a `training_step()` function

    Researcher may have to add extra dependencies/python imports, by using `add_dependencies` method.

    Attributes:
        dataset_path: The path that indicates where dataset has been stored
        pre_processes: Preprocess functions that will be applied to the
            training data at the beginning of the training routine.
        training_data_loader: Data loader used in the training routine.
        testing_data_loader: Data loader used in the validation routine.
        correction_state: an OrderedDict of {'parameter name': torch.Tensor} where the keys correspond to the names of
            the model parameters contained in self._model.named_parameters(), and the values correspond to the
            correction to be applied to that parameter.
    """

    def __init__(self):
        """ Construct training plan """

        super().__init__()

        self.__type = TrainingPlans.TorchTrainingPlan

        # Differential privacy support
        self._dp_controller = None

        self._optimizer = None
        self._model = None

        self._training_args = None
        self._model_args = None
        self._optimizer_args = None
        self._use_gpu = False

        self._batch_maxnum = 100
        self._fedprox_mu = None
        self._log_interval = 10
        self._epochs = 1
        self._dry_run = False
        self._num_updates = None

        self.correction_state = OrderedDict()
        self.aggregator_name = None

        # TODO : add random seed init
        # self.random_seed_params = None
        # self.random_seed_shuffling_data = None

        # device to use: cpu/gpu
        # - all operations except training only use cpu
        # - researcher doesn't request to use gpu by default
        self._device_init = "cpu"
        self._device = self._device_init

        # list dependencies of the model
        self.add_dependency(["import torch",
                             "import torch.nn as nn",
                             "import torch.nn.functional as F",
                             "from fedbiomed.common.training_plans import TorchTrainingPlan",
                             "from fedbiomed.common.data import DataManager",
                             "from fedbiomed.common.constants import ProcessTypes",
                             "from torch.utils.data import DataLoader",
                             "from torchvision import datasets, transforms"
                             ])

        # Aggregated model parameters
        self._init_params: List[torch.Tensor] = None

    def post_init(
            self,
            model_args: Dict[str, Any],
            training_args: TrainingArgs,
            aggregator_args: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Process model, training and optimizer arguments.

        Args:
            model_args: Arguments defined to instantiate the wrapped model.
            training_args: Arguments that are used in training routines
                such as epoch, dry_run etc.
                Please see [`TrainingArgs`][fedbiomed.common.training_args.TrainingArgs]
            aggregator_args: Arguments managed by and shared with the
                researcher-side aggregator.

        Raises:
            FedbiomedTrainingPlanError: If the provided arguments do not
                match expectations, or if the optimizer, model and dependencies
                configuration goes wrong.
        """

        self._model_args = model_args
        self._optimizer_args = training_args.optimizer_arguments() or {}
        self._training_args = training_args.pure_training_arguments()
        self._use_gpu = self._training_args.get('use_gpu')
        self._batch_maxnum = self._training_args.get('batch_maxnum')

        self._log_interval = self._training_args.get('log_interval')
        self._epochs = self._training_args.get('epochs')
        self._num_updates = self._training_args.get('num_updates', 1)
        self._dry_run = self._training_args.get('dry_run')

        # aggregator args
        self._fedprox_mu = self._training_args.get('fedprox_mu')
        # TODO: put fedprox mu inside strategy_args
        self._aggregator_args = aggregator_args or {}

        self.set_aggregator_args(self._aggregator_args)
        # self.aggregator_name = self._aggregator_args.get('aggregator_name')
        # FIXME: we should have a AggregatorHandler that handles aggregator args

        self._dp_controller = DPController(training_args.dp_arguments() or None)

        # Add dependencies
        self._configure_dependencies()

        # Configure model and optimizer
        self._configure_model_and_optimizer()

    @abstractmethod
    def init_model(self):
        """Abstract method where model should be defined """
        pass

    @abstractmethod
    def training_step(self):
        """Abstract method, all subclasses must provide a training_step.
        """
        pass

    @abstractmethod
    def training_data(self):
        """Abstract method to return training data"""
        pass

    def model(self):
        return self._model

    def optimizer(self):
        return self._optimizer

    def model_args(self) -> Dict:
        """Retrieves model args

        Returns:
            Model arguments arguments
        """
        return self._model_args

    def get_learning_rate(self) -> List[float]:
        """Gets learning rate from value set in optimizer.

        !!! warning
            This function gathers the base learning rate applied to the model weights,
            including alterations due to any LR scheduler. However, it does not catch
            any adaptive component, e.g. due to RMSProp, Adam or such.

        Returns:
            List[float]: list of single learning rate or multiple learning rates
                (as many as the number of the layers contained in the model)
        """
        if self._optimizer is None:
            raise FedbiomedTrainingPlanError(f"{ErrorNumbers.FB605.value}: Optimizer not found, please call "
                                             f"`init_optimizer beforehand")
        learning_rates = []
        params = self._optimizer.param_groups
        for param in params:
            learning_rates.append(param['lr'])
        return learning_rates

    def update_optimizer_args(self) -> Dict:
        """
        Updates `_optimizer_args` variable. Can prove useful
        to retrieve optimizer parameters after having trained a
        model, parameters which may have changed during training (eg learning rate).

        Updated arguments:
         - learning_rate

        Returns:
            Dict: updated `_optimizer_args`
        """
        if self._optimizer_args is None:
            self._optimizer_args = {}
        self._optimizer_args['lr'] = self.get_learning_rate()
        return self._optimizer_args

    def get_model_params(self) -> OrderedDict:
        return self._model.state_dict()

    def training_args(self) -> Dict:
        """Retrieves training args

        Returns:
            Training arguments
        """
        return self._training_args

    def optimizer_args(self) -> Dict:
        """Retrieves optimizer arguments

        Returns:
            Optimizer arguments
        """
        self.update_optimizer_args()  # update `optimizer_args` (eg after training)
        return self._optimizer_args

    def initial_parameters(self) -> Dict:
        """Returns initial parameters without DP or training applied

        Returns:
            State dictionary of torch Module
        """
        return self._init_params

    def init_optimizer(self):
        """Abstract method for declaring optimizer by default """
        try:
            self._optimizer = torch.optim.Adam(self._model.parameters(), **self._optimizer_args)
        except AttributeError as e:
            raise FedbiomedTrainingPlanError(f"{ErrorNumbers.FB605.value}: Invalid argument for default "
                                             f"optimizer Adam. Error: {e}")

        return self._optimizer

    def type(self) -> TrainingPlans.TorchTrainingPlan:
        """ Gets training plan type"""
        return self.__type

    def _configure_model_and_optimizer(self):
        """Configures model and optimizers before training """

        # Message to format for unexpected argument definitions in special methods
        method_error = \
            ErrorNumbers.FB605.value + ": Special method `{method}` has more than one argument: {keys}. This method " \
                                       "can not have more than one argument/parameter (for {prefix} arguments) or " \
                                       "method can be defined without argument and `{alternative}` can be used for " \
                                       "accessing {prefix} arguments defined in the experiment."

        # Get model defined by user -----------------------------------------------------------------------------
        init_model_spec = get_method_spec(self.init_model)
        if not init_model_spec:
            self._model = self.init_model()
        elif len(init_model_spec.keys()) == 1:
            self._model = self.init_model(self._model_args)
        else:
            raise FedbiomedTrainingPlanError(method_error.format(prefix="model",
                                                                 method="init_model",
                                                                 keys=list(init_model_spec.keys()),
                                                                 alternative="self.model_args()"))

        # Validate and fix model
        self._model = self._dp_controller.validate_and_fix_model(self._model)

        # Validate model
        if not isinstance(self._model, nn.Module):
            raise FedbiomedTrainingPlanError(f"{ErrorNumbers.FB605.value}: Model should be an instance of `nn.Module`")

        # Get optimizer defined by researcher ---------------------------------------------------------------------
        init_optim_spec = get_method_spec(self.init_optimizer)
        if not init_optim_spec:
            self._optimizer = self.init_optimizer()
        elif len(init_optim_spec.keys()) == 1:
            self._optimizer = self.init_optimizer(self._optimizer_args)
        else:
            raise FedbiomedTrainingPlanError(method_error.format(prefix="optimizer",
                                                                 method="init_optimizer",
                                                                 keys=list(init_optim_spec.keys()),
                                                                 alternative="self.optimizer_args()"))

        # Validate optimizer
        if not isinstance(self._optimizer, torch.optim.Optimizer):
            raise FedbiomedTrainingPlanError(f"{ErrorNumbers.FB605.value}: Optimizer should torch base optimizer.")

    def _set_device(self, use_gpu: Union[bool, None], node_args: dict):
        """Set device (CPU, GPU) that will be used for training, based on `node_args`

        Args:
            use_gpu: researcher requests to use GPU (or not)
            node_args: command line arguments for node
        """

        # set default values for node args
        if 'gpu' not in node_args:
            node_args['gpu'] = False
        if 'gpu_num' not in node_args:
            node_args['gpu_num'] = None
        if 'gpu_only' not in node_args:
            node_args['gpu_only'] = False

        # Training uses gpu if it exists on node and
        # - either proposed by node + requested by training plan
        # - or forced by node
        cuda_available = torch.cuda.is_available()
        if use_gpu is None:
            use_gpu = self._use_gpu
        use_cuda = cuda_available and ((use_gpu and node_args['gpu']) or node_args['gpu_only'])

        if node_args['gpu_only'] and not cuda_available:
            logger.error('Node wants to force model training on GPU, but no GPU is available')
        if use_cuda and not use_gpu:
            logger.warning('Node enforces model training on GPU, though it is not requested by researcher')
        if not use_cuda and use_gpu:
            logger.warning('Node training model on CPU, though researcher requested GPU')

        # Set device for training
        self._device = "cpu"
        if use_cuda:
            if node_args['gpu_num'] is not None:
                if node_args['gpu_num'] in range(torch.cuda.device_count()):
                    self._device = "cuda:" + str(node_args['gpu_num'])
                else:
                    logger.warning(f"Bad GPU number {node_args['gpu_num']}, using default GPU")
                    self._device = "cuda"
            else:
                self._device = "cuda"

        logger.debug(f"Using device {self._device} for training "
                     f"(cuda_available={cuda_available}, gpu={node_args['gpu']}, "
                     f"gpu_only={node_args['gpu_only']}, "
                     f"use_gpu={use_gpu}, gpu_num={node_args['gpu_num']})")

    def send_to_device(self,
                       to_send: Union[torch.Tensor, list, tuple, dict],
                       device: torch.device
                       ):
        """Send inputs to correct device for training.

        Recursively traverses lists, tuples and dicts until it meets a torch Tensor, then sends the Tensor
        to the specified device.

        Args:
            to_send: the data to be sent to the device.
            device: the device to send the data to.

        Raises:
           FedbiomedTrainingPlanError: when to_send is not the correct type
        """
        if isinstance(to_send, torch.Tensor):
            return to_send.to(device)
        elif isinstance(to_send, dict):
            return {key: self.send_to_device(val, device) for key, val in to_send.items()}
        elif isinstance(to_send, tuple):
            return tuple(self.send_to_device(d, device) for d in to_send)
        elif isinstance(to_send, list):
            return [self.send_to_device(d, device) for d in to_send]
        else:
            raise FedbiomedTrainingPlanError(f'{ErrorNumbers.FB310.value} cannot send data to device. '
                                             f'Data must be a torch Tensor or a list, tuple or dict '
                                             f'ultimately containing Tensors.')

    def training_routine(self,
                         history_monitor: Any = None,
                         node_args: Union[dict, None] = None,
                         ):
        # FIXME: add betas parameters for ADAM solver + momentum for SGD
        # FIXME 2: remove parameters specific for validation specified in the
        # training routine
        """Training routine procedure.

        End-user should define;

        - a `training_data()` function defining how sampling / handling data in node's dataset is done. It should
            return a generator able to output tuple (batch_idx, (data, targets)) that is iterable for each batch.
        - a `training_step()` function defining how cost is computed. It should output loss values for backpropagation.

        Args:
            history_monitor: Monitor handler for real-time feed. Defined by the Node and can't be overwritten
            node_args: command line arguments for node. Can include:
                - `gpu (bool)`: propose use a GPU device if any is available. Default False.
                - `gpu_num (Union[int, None])`: if not None, use the specified GPU device instead of default
                    GPU device if this GPU device is available. Default None.
                - `gpu_only (bool)`: force use of a GPU device if any available, even if researcher
                    doesn't request for using a GPU. Default False.
        """

        self._model.train()  # pytorch switch for training

        # set correct type for node args
        node_args = {} if not isinstance(node_args, dict) else node_args

        # send all model to device, ensures having all the requested tensors
        self._set_device(self._use_gpu, node_args)
        self._model.to(self._device)

        # Run preprocess when everything is ready before the training
        self._preprocess()

        # initial aggregated model parameters
        self._init_params = deepcopy(list(self._model.parameters()))

        # DP actions
        self._model, self._optimizer, self.training_data_loader = \
            self._dp_controller.before_training(self._model, self._optimizer, self.training_data_loader)

        # set number of training loop iterations
        num_epochs, num_batches_in_last_epoch, num_batches_per_epoch = self._n_training_iterations(self.training_args())

        # setup accounting for monitoring purposes
        total_batches_to_be_observed: int = num_epochs * num_batches_per_epoch + num_batches_in_last_epoch
        total_n_samples_to_be_observed: int = self._training_args['batch_size'] * total_batches_to_be_observed
        n_samples_observed_till_now: int = 0

        # Training loop iterations
        # we always iterate over one additional (possibly empty!) epoch to account for remainder batches
        for epoch in range(1, num_epochs + 2):
            _n_batches_in_this_epoch: int = num_batches_per_epoch if epoch <= num_epochs else num_batches_in_last_epoch
            training_iter: Iterator = iter(self.training_data_loader)
            for batch_idx in range(1, _n_batches_in_this_epoch + 1):
                # retrieve data and target
                data, target = next(training_iter)

                # handle training on accelerator devices
                data, target = self.send_to_device(data, self._device), self.send_to_device(target, self._device)

                # update accounting for number of observed samples
                n_samples_observed_till_now += self.__infer_batch_size(data)

                # train this batch
                corrected_loss, loss = self._train_over_batch(data, target)

                # Monitoring if log_interval or last batch in epoch
                if (batch_idx % self._log_interval == 0 or
                        self._dry_run or
                        batch_idx >= _n_batches_in_this_epoch or  # last batch
                        batch_idx == 1):  # first batch
                    # The node sees the "real" total number of samples observed until now
                    logger.debug('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                        epoch,
                        n_samples_observed_till_now,
                        total_n_samples_to_be_observed,
                        100 * n_samples_observed_till_now / total_n_samples_to_be_observed,
                        loss.item()))

                    # Send scalar values via general/feedback topic
                    if history_monitor is not None:
                        # the researcher only sees the average value of samples observed until now
                        history_monitor.add_scalar(metric={'Loss': loss.item()},
                                                   iteration=(epoch - 1) * num_batches_per_epoch + batch_idx,
                                                   epoch=epoch,
                                                   train=True,
                                                   num_batches=total_batches_to_be_observed,
                                                   total_samples=total_n_samples_to_be_observed,
                                                   batch_samples=self._training_args['batch_size'])

                # Handle dry run mode
                if self._dry_run:
                    self._model.to(self._device_init)
                    torch.cuda.empty_cache()
                    return

        # release gpu usage as much as possible though:
        # - it should be done by deleting the object
        # - and some gpu memory remains used until process (cuda kernel ?) finishes

        self._model.to(self._device_init)
        torch.cuda.empty_cache()

    def _train_over_batch(self, data: ModelInputType, target: ModelInputType) -> Tuple[torch.Tensor, torch.Tensor]:
        """Train the model over a single batch of data.

        This function handles all the torch-specific logic concerning model training, including backward propagation,
        aggregator-specific correction terms, and optimizer stepping.

        Args:
            data: the input data to the model
            target: the training labels

        Returns:
            corrected loss: the loss value used for backward propagation, including any correction terms
            loss: the uncorrected loss for reporting
        """
        # zero-out gradients
        self._optimizer.zero_grad()

        # compute loss
        loss = self.training_step(data, target)  # raises an exception if not provided
        corrected_loss = torch.clone(loss)

        # If FedProx is enabled: use regularized loss function
        if self._fedprox_mu is not None:
            corrected_loss += float(self._fedprox_mu) / 2 * self.__norm_l2()

        # Run the backward pass to compute parameters' gradients
        corrected_loss.backward()

        # If Scaffold is used: apply corrections to the gradients
        if self.aggregator_name is not None and self.aggregator_name.lower() == "scaffold":
            for name, param in self._model.named_parameters():
                correction = self.correction_state.get(name)
                if correction is not None:
                    param.grad.add_(correction.to(param.grad.device))

        # Have the optimizer collect, refine and apply gradients
        self._optimizer.step()

        return corrected_loss, loss

    def testing_routine(
            self,
            metric: Optional[MetricTypes],
            metric_args: Dict[str, Any],
            history_monitor: Optional['HistoryMonitor'],
            before_train: bool
    ) -> None:
        """Evaluation routine, to be called once per round.

        !!! info "Note"
            If the training plan implements a `testing_step` method
            (the signature of which is func(data, target) -> metrics)
            then it will be used rather than the input metric.

        Args:
            metric: The metric used for validation.
                If None, use MetricTypes.ACCURACY.
            history_monitor: HistoryMonitor instance,
                used to record computed metrics and communicate them to
                the researcher (server).
            before_train: Whether the evaluation is being performed
                before local training occurs, of afterwards. This is merely
                reported back through `history_monitor`.
        """
        if not isinstance(self._model, torch.nn.Module):
            msg = (
                f"{ErrorNumbers.FB320.value}: model should be a torch "
                f"nn.Module, but is of type {type(self._model)}"
            )
            logger.critical(msg)
            raise FedbiomedTrainingPlanError(msg)
        try:
            self._model.eval()  # pytorch switch for model inference-mode
            with torch.no_grad():
                super().testing_routine(
                    metric, metric_args, history_monitor, before_train
                )
        finally:
            self._model.train()  # restore training behaviors

    def predict(
            self,
            data: Any,
    ) -> np.ndarray:
        """Return model predictions for a given batch of input features.

        This method is called as part of `testing_routine`, to compute
        predictions based on which evaluation metrics are computed. It
        will however be skipped if a `testing_step` method is attached
        to the training plan, than wraps together a custom routine to
        compute an output metric directly from a (data, target) batch.

        Args:
            data: Array-like (or tensor) structure containing batched
                input features.

        Returns:
            np.ndarray: Output predictions, converted to a numpy array
                (as per the `fedbiomed.common.metrics.Metrics` specs).
        """
        with torch.no_grad():
            pred = self._model(data)
        return pred.numpy()

    # provided by fedbiomed
    def save(self, filename: str, params: dict = None) -> None:
        """Save the torch training parameters from this training plan or from given `params` to a file

        Args:
            filename (str): Path to the destination file
            params (dict): Parameters to save to a file, should be structured as a torch state_dict()

        """
        if params is not None:
            return torch.save(params, filename)
        else:
            return torch.save(self._model.state_dict(), filename)

    # provided by fedbiomed
    def load(self, filename: str, to_params: bool = False) -> dict:
        """Load the torch training parameters to this training plan or to a data structure from a file

        Args:
            filename: path to the source file
            to_params: if False, load params to this pytorch object; if True load params to a data structure

        Returns:
            Contains parameters
        """
        params = torch.load(filename)
        if to_params is False:
            self._model.load_state_dict(params)
        return params

    def set_aggregator_args(self, aggregator_args: Dict[str, Any]):
        """Handles and loads aggregators arguments sent through MQTT and
        file exchanged system. If sent through file exchanged system, loads the arguments.

        Args:
            aggregator_args (Dict[str, Any]): dictionary mapping aggregator argument name with its value (eg
            'aggregator_correction' with correction states)
        """
        self.aggregator_name = aggregator_args.get('aggregator_name') or self.aggregator_name

        for arg_name, aggregator_arg in aggregator_args.items():
            if arg_name == 'aggregator_correction' and aggregator_arg.get('param_path', False):
                # FIXME: this is too specific to Scaffold. Should be redesigned, or handled
                # by an aggregator handler that contains all keys for all strategies implemented
                # in fedbiomed

                # setattr(self, arg_name, aggregator_arg)
                # here we ae loading all args that have been sent from file exchange system
                self.correction_state = torch.load(aggregator_arg.get('param_path'))

    def after_training_params(self) -> dict:
        """Retrieve parameters after training is done

        Call the user defined postprocess function:
            - if provided, the function is part of pytorch model defined by the researcher
            - and expect the model parameters as argument

        Returns:
            The state_dict of the model, or modified state_dict if preprocess is present
        """

        # Check whether postprocess method exists, and use it

        params = self._model.state_dict()
        if hasattr(self, 'postprocess'):
            logger.debug("running model.postprocess() method")
            try:
                params = self.postprocess(self._model.state_dict())  # Post process
            except Exception as e:
                raise FedbiomedTrainingPlanError(f"{ErrorNumbers.FB605.value}: Error while running post process "
                                                 f"{e}")

        params = self._dp_controller.after_training(params)
        return params

    def __norm_l2(self) -> float:
        """Regularize L2 that is used by FedProx optimization

        Returns:
            L2 norm of model parameters (before local training)
        """
        norm = 0

        for current_model, init_model in zip(self._model.parameters(), self._init_params):
            norm += ((current_model - init_model) ** 2).sum()
        return norm

    @staticmethod
    def __infer_batch_size(data: Union[dict, torch.Tensor]) -> int:
        """Utility function to guess batch size from data.

        This function is a temporary fix needed to handle the case where
        Opacus changes the batch_size dynamically, without communicating
        it in any way.

        This will be improved by issue #422.

        Returns:
            the batch size for the input data
        """
        if isinstance(data, dict):
            # case `data` is a dict (eg {'modality1': data1, 'modality2': data2}):
            # compute length of the first modality
            batch_size = len(list(data.values())[0])
        else:
            # case `data` is a Tensor
            batch_size = len(data)
        return batch_size
