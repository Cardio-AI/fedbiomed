# This file is originally part of Fed-BioMed
# SPDX-License-Identifier: Apache-2.0

"""Manage the training part of the experiment."""

import atexit
import copy
import importlib
import inspect
import os
import re
import sys
import shutil
import tempfile
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

import validators

from fedbiomed.common.constants import TrainingPlanApprovalStatus
from fedbiomed.common.exceptions import FedbiomedRepositoryError, FedbiomedDataQualityCheckError
from fedbiomed.common.logger import logger
from fedbiomed.common.repository import Repository
from fedbiomed.common.serializer import Serializer
from fedbiomed.common.training_args import TrainingArgs

from fedbiomed.researcher.datasets import FederatedDataSet
from fedbiomed.researcher.environ import environ
from fedbiomed.researcher.filetools import create_unique_link, create_unique_file_link
from fedbiomed.researcher.requests import Requests
from fedbiomed.researcher.responses import Responses


class Job:
    """
    Represents the entity that manage the training part at  the nodes level

    Starts a message queue, loads python model file created by researcher (through
    [`training_plans`][fedbiomed.common.training_plans]) and saves the loaded model in a temporary file
    (under the filename '<TEMP_DIR>/my_model_<random_id>.py').

    """

    def __init__(self,
                 reqs: Requests = None,
                 nodes: dict = None,
                 training_plan_class: Union[Type[Callable], str] = None,
                 training_plan_path: str = None,
                 training_args: TrainingArgs = None,
                 model_args: dict = None,
                 data: FederatedDataSet = None,
                 keep_files_dir: str = None):

        """ Constructor of the class

        Args:
            reqs: Researcher's requests assigned to nodes. Defaults to None.
            nodes: A dict of node_id containing the nodes used for training
            training_plan_class: instance or class of the TrainingPlan.
            training_plan_path: Path to file containing model class code
            training_args: Contains training parameters; lr, epochs, batch_size.
            model_args: Contains output and input feature dimension
            data: Federated datasets
            keep_files_dir: Directory for storing files created by the job that we want to keep beyond the execution
                of the job. Defaults to None, files are not kept after the end of the job.

        Raises:
            NameError: If model is not defined or if the class can not to be inspected
        """

        self._id = str(uuid.uuid4())  # creating a unique job id
        self._researcher_id = environ['RESEARCHER_ID']
        self._repository_args = {}
        self._training_args = training_args
        self._model_args = model_args
        self._nodes = nodes
        self._training_replies = {}  # will contain all node replies for every round
        self._model_file = None  # path to local file containing model code
        self._model_params_file = ""  # path to local file containing current version of aggregated params
        self._training_plan_class = training_plan_class
        # self._training_plan = None  # declared below, as a TrainingPlan instance
        self._aggregator_args = None

        if keep_files_dir:
            self._keep_files_dir = keep_files_dir
        else:
            self._keep_files_dir = tempfile.mkdtemp(prefix=environ['TMP_DIR'])
            atexit.register(lambda: shutil.rmtree(self._keep_files_dir))  # remove directory
            # when script ends running (replace
            # `with tempfile.TemporaryDirectory(dir=environ['TMP_DIR']) as self._keep_files_dir: `)

        if reqs is None:
            self._reqs = Requests()
        else:
            self._reqs = reqs

        self.last_msg = None
        self._data = data

        # Check dataset quality
        if self._data is not None:
            self.check_data_quality()

        # Model is mandatory
        if self._training_plan_class is None:
            mess = "Missing training plan class name or instance in Job arguments"
            logger.critical(mess)
            raise NameError(mess)

        # handle case when model is in a file
        if training_plan_path is not None:
            try:
                # import model from python file

                model_module = os.path.basename(training_plan_path)
                model_module = re.search("(.*)\.py$", model_module).group(1)
                sys.path.insert(0, os.path.dirname(training_plan_path))

                module = importlib.import_module(model_module)
                tr_class = getattr(module, self._training_plan_class)
                self._training_plan_class = tr_class
                sys.path.pop(0)

            except Exception as e:
                e = sys.exc_info()
                logger.critical(f"Cannot import class {self._training_plan_class} from "
                                f"path {training_plan_path} - Error: {str(e)}")
                sys.exit(-1)

        # check class is defined
        try:
            _ = inspect.isclass(self._training_plan_class)
        except NameError:
            mess = f"Cannot find training plan for Job, training plan class {self._training_plan_class} is not defined"
            logger.critical(mess)
            raise NameError(mess)

        # create/save TrainingPlan instance
        if inspect.isclass(self._training_plan_class):
            self._training_plan = self._training_plan_class()  # contains TrainingPlan

        else:
            self._training_plan = self._training_plan_class
        self._training_plan.post_init(model_args={} if self._model_args is None else self._model_args,
                                      training_args=self._training_args)

    @property
    def id(self):
        return self._id

    @property
    def training_plan_name(self):
        return self._training_plan.__class__.__name__

    @property
    def training_plan(self):
        return self._training_plan

    @property
    def training_plan_file(self):
        return self._training_plan_file

    @property
    def requests(self):
        return self._reqs

    @property
    def aggregator_args(self):
        return self._aggregator_args

    @property
    def nodes(self):
        return self._nodes

    @nodes.setter
    def nodes(self, nodes: dict):
        self._nodes = nodes

    @property
    def training_replies(self):
        return self._training_replies

    @property
    def training_args(self):
        return self._training_args.dict()

    @training_args.setter
    def training_args(self, training_args: TrainingArgs):
        self._training_args = training_args

    def check_training_plan_is_approved_by_nodes(self) -> List:

        """ Checks whether model is approved or not.

        This method sends `training-plan-status` request to the nodes. It should be run before running experiment.
        So, researchers can find out if their model has been approved

        """

        message = {
            'researcher_id': self._researcher_id,
            'job_id': self._id,
            'training_plan': self._training_plan.source(),
            'command': 'training-plan-status'
        }

        responses = Responses([])
        replied_nodes = []
        node_ids = self._data.node_ids()

        # Send message to each node that has been found after dataset search request
        for cli in node_ids:
            logger.info('Sending request to node ' +
                        str(cli) + " to check model is approved or not")
            self._reqs.send_message(message, cli)

        # Wait for responses
        for resp in self._reqs.get_responses(look_for_commands=['training-plan-status'], only_successful=False):
            responses.append(resp)
            replied_nodes.append(resp.get('node_id'))

            if resp.get('success') is True:
                if resp.get('approval_obligation') is True:
                    if resp.get('status') == TrainingPlanApprovalStatus.APPROVED.value:
                        logger.info(f'Training plan has been approved by the node: {resp.get("node_id")}')
                    else:
                        logger.warning(f'Training plan has NOT been approved by the node: {resp.get("node_id")}.' +
                                       f'Training plan status : {resp.get("status")}')
                else:
                    logger.info(f'Training plan approval is not required by the node: {resp.get("node_id")}')
            else:
                logger.warning(f"Node : {resp.get('node_id')} : {resp.get('msg')}")

        # Get the nodes that haven't replied training-plan-status request
        non_replied_nodes = list(set(node_ids) - set(replied_nodes))
        if non_replied_nodes:
            logger.warning(f"Request for checking training plan status hasn't been replied \
                             by the nodes: {non_replied_nodes}. You might get error \
                                 while running your experiment. ")

        return responses

    # TODO: This method should change in the future or as soon as we implement other of strategies different
    #   than DefaultStrategy

    def waiting_for_nodes(self, responses: Responses) -> bool:
        """ Verifies if all nodes involved in the job are present and Responding

        Args:
            responses: contains message answers

        Returns:
            False if all nodes are present in the Responses object. True if waiting for at least one node.
        """
        try:
            nodes_done = set(responses.dataframe()['node_id'])
        except KeyError:
            nodes_done = set()

        return not nodes_done == set(self._nodes)
    
    def _get_training_testing_results(self, round_, timer: Dict) -> None:
        """"Waits for training replies 
        
        args: 
            round_: Training round
            training: If True the round is for training, otherwise for testing/validation
            timer: Stores time elapsed on the researcher side 
        """

        # Recollect models trained
        self._training_replies[round_] = Responses([])
        while self.waiting_for_nodes(self._training_replies[round_]):
            
            # collect nodes responses from researcher request 'train'
            models_done = self._reqs.get_responses(look_for_commands=['train', 'error'], only_successful=False)
            
            for m in models_done.data():  # retrieve all models
                # (there should have as many models done as nodes)
                # manage error messages during training
                if m['command'] == 'error':
                    if m['extra_msg']:
                        logger.info(f"Error message received during training: {m['errnum']} "
                                    f"- {str(m['extra_msg'])}")
                    else:
                        logger.info(f"Error message received during training: {m['errnum']}")

                    faulty_node = m['node_id']  # remove the faulty node from the list

                    if faulty_node not in list(self._nodes):
                        logger.warning(f"Error message from {faulty_node} ignored, since this node is not part ot "
                                       f"the training any mode")
                        continue

                    self._nodes.remove(faulty_node)
                    continue

                # only consider replies for our request
                if m['researcher_id'] != environ['RESEARCHER_ID'] or \
                        m['job_id'] != self._id or m['node_id'] not in list(self._nodes):
                    continue

                # manage training failure for this job
                if not m['success']:
                    logger.error(f"Training failed for node {m['node_id']}: {m['msg']}")
                    self._nodes.remove(m['node_id'])  # remove the faulty node from the list
                    continue

                rtime_total = time.perf_counter() - timer[m['node_id']]

                # TODO: could choose completely different name/structure for
                timing = m['timing']
                timing['rtime_total'] = rtime_total

                response = Responses({
                    'success': m['success'],
                    'msg': m['msg'],
                    'dataset_id': m['dataset_id'],
                    'node_id': m['node_id'],
                    # 'params_path': params_path, # TODO: check if it is useful to save params
                    'params': m["params"],
                    'optimizer_args': m["optimizer_args"],
                    'optim_aux_var': m["optim_aux_var"],
                    'sample_size': m["sample_size"],
                    'encryption_factor': m["encryption_factor"],
                    'timing': timing,
                })
                
                self._training_replies[round_].append(response)


    def start_nodes_training_round(
        self,
        round_: int,
        aggregator_args: Dict[str, Dict[str, Any]],
        secagg_arguments: Union[Dict, None] = None,
        do_training: bool = True,
        optim_aux_var: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """ Sends training request to nodes and waits for the responses

        Args:
            round_: current number of round the algorithm is performing (a round is considered to be all the
                training steps of a federated model between 2 aggregations).
            aggregator_args_thr_msg: dictionary containing some metadata about the aggregation
                strategy, useful to transfer some data when it's required by am aggregator. First key should be the
                node_id, and sub-dictionary sould be parameters to be sent through MQTT messaging system
            aggregator_args_thr_files: dictionary containing metadata about aggregation strategy, to be transferred
                via the Repository's HTTP API, as opposed to the mqtt system. Format is the same as
                aggregator_args_thr_msg .
            secagg_arguments: Secure aggregation ServerKey context id
            do_training: if False, skip training in this round (do only validation). Defaults to True.
            optim_aux_var: Auxiliary variables of the researcher-side Optimizer, if any.
                Note that such variables may only be used if both the Experiment and node-side training plan
                hold a declearn-based [Optimizer][fedbiomed.common.optimizers.Optimizer], and their plug-ins
                are coherent with each other as to expected information exchange.
        """

        # Assign empty dict to secagg arguments if it is None
        secagg_arguments = {} if secagg_arguments is None else secagg_arguments 

        msg = {
            'researcher_id': self._researcher_id,
            'job_id': self._id,
            'training_args': self._training_args.dict(),
            'training': do_training,
            'model_args': self._model_args,
            'round': round_,
            'training_plan': self._training_plan.source(),
            'training_plan_class': self.training_plan_name,
            'params': self._get_model_params(),
            'secagg_servkey_id': secagg_arguments.get('secagg_servkey_id'),
            'secagg_biprime_id': secagg_arguments.get('secagg_biprime_id'),
            'secagg_random': secagg_arguments.get('secagg_random'),
            'secagg_clipping_range': secagg_arguments.get('secagg_clipping_range'),
            'command': 'train',
            'aggregator_args': {},
            'aux_vars': [],
        }

        timer = {}

        # Upload optimizer auxiliary variables, when there are.
        if do_training and optim_aux_var:
            aux_shared, aux_bynode = (
                self._prepare_agg_optimizer_aux_var(optim_aux_var, nodes=list(self._nodes))
            )

        else:
            aux_shared = None
            aux_bynode = {}

        # Loop over nodes, add node specific data and send train request
        for node in self._nodes:
            msg['dataset_id'] = self._data.data()[node]['dataset_id']
            msg['aux_vars'] = [aux_shared, aux_bynode.get(node, None)]

            # FIXME: There might be another node join recently
            msg['aggregator_args'] = aggregator_args.get(node, {}) if aggregator_args else {}
            self._log_round_info(node=node, message=msg, training=do_training)

            timer[node] = time.perf_counter()

            # Sends training request
            self._reqs.send_message(msg, node)  # send request to node

        self._get_training_testing_results(round_=round_, timer=timer)

        # return the list of nodes which answered because nodes in error have been removed
        return self._nodes

    @staticmethod
    def _prepare_agg_optimizer_aux_var(
        aux_var: Dict[str, Dict[str, Any]],
        nodes: List[uuid.UUID],
    ) -> Tuple[
        Dict[str, Dict[str, Any]],
        Dict[uuid.UUID, Dict[str, Dict[str, Any]]],
    ]:
        """Collect and structure researcher-side Optimizer auxiliary variables.

        Args:
            aux_var: Auxiliary variables with to structure into multiple dicts,
                from `{mod_name: (shared_dict | {node_id: node_dict})}` to
                `{mod_name: shared_dict}` & `{node_id: {mod_name: node_dict}}`.
            nodes: Ids of the nodes to whom auxiliary variables should be
                sent. This is used to drop information of non-participating
                nodes.

        Returns:
            aux_shared: Dict containing auxiliary variables that are shared
                across all nodes, with `{mod_name: shared_dict}` format.
            aux_bynode: Dict containing node-wise dicts of node-specific
                auxiliary variables, with `{node_id: {mod_name: node_dict}}`
                format.
        """
        aux_shared = {}  # type: Dict[str, Dict[str, Any]]
        aux_bynode = {}  # type: Dict[uuid.UUID, Dict[str, Dict[str, Any]]]
        # Iterate over nodes and plug-in-module-wise auxiliary variables.
        for node_id in nodes:
            aux_bynode[node_id] = {}
            for mod_name, mod_info in aux_var.items():
                # Case of node-specfic information.
                if node_aux := mod_info.get(str(node_id)):
                    aux_bynode[node_id][mod_name] = node_aux
                # Case of global information shared with all nodes.
                elif mod_name not in aux_shared:
                    aux_shared[mod_name] = mod_info
        # Return the restructured auxiliary variables dicts.
        return aux_shared, aux_bynode

    def extract_received_optimizer_aux_var_from_round(
        self,
        round_id: int,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Restructure the received auxiliary variables (if any) from a round.

        Args:
            round_id: Index of the round, replies from which to parse through.

        Returns:
            Dict of auxiliary variables, collating node-wise information, with
            format `{mod_name: {node_id: node_dict}}`.
        """
        aux_var = {}  # type: Dict[str, Dict[str, Dict[str, Any]]]
        for reply in self.training_replies[round_id]:
            node_id = reply["node_id"]
            node_av = reply.get("optim_aux_var", {})
            for module, params in node_av.items():
                aux_var.setdefault(module, {})[node_id] = params
        return aux_var

    def _get_model_params(self) -> Dict[str, Any]:
        """Gets model parameters form the training plan"""
        return self._training_plan.get_model_params()

    def _load_and_set_model_params_from_file(self, path: str) -> bool:
        """Loads model parameters from given path
        
        Args: 
            path: The path where model parameters are saved
        """
        params = Serializer.load(path)["model_weights"]
        self._training_plan.set_model_params(params)

        return True
    
    def _serialize_model_params(self) -> Dict[str, Any]:
        """Serialize and saves model parameters
        Args:

            params: Model parameters to be serialized and saved
        """
        return Serializer.dumps(self._get_model_params())

    def _update_model_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """"Updates the parameters of the model by given params
        
        Args:
            params: Parameters that are going to be loaded into model

        """
        self._training_plan.set_model_params(params)

    def _log_round_info(self, node: str, message: Dict, training: True) -> None:
        """Logs round details
        
        Args: 
            node: Node id
            message: Message that will be sent to the node
            training: If True round will do training, otherwise it is the last validation round
        """

        if not training:
            logger.info(f'\033[1mSending request\033[0m \n'
                        f'\t\t\t\t\t\033[1m To\033[0m: {str(node)} \n'
                        f'\t\t\t\t\t\033[1m Request: \033[0m:Perform final validation on '
                        f'aggregated parameters \n {5 * "-------------"}')
        else:
            # msg_print = {key: value for key, value in message.items()
            #                 if key != 'aggregator_args' and logger.level != "DEBUG" }
            logger.info(f'\033[1mSending request\033[0m \n'
                        f'\t\t\t\t\t\033[1m To\033[0m: {str(node)} \n'
                        f'\t\t\t\t\t\033[1m Request: \033[0m: TRAIN'
                        f'\n {5 * "-------------"}')

    def update_parameters(
        self,
        params: Optional[Dict[str, Any]]
    ) -> Tuple[str, str]:
        """Update model parameters
        
        Args: 
            params: Aggregated model parameters
        """
        self._update_model_params(params)
        filename = os.path.join(self._keep_files_dir, f"aggregated_params_{uuid.uuid4()}.mpk")
        Serializer.dump(params, filename)
        self._model_params_file = filename

        return filename

    def save_state(self, breakpoint_path: str) -> dict:
        """Creates current state of the job to be included in a breakpoint.

        Includes creating links to files included in the job state.

        Args:
            breakpoint_path: path to the existing breakpoint directory

        Returns:
            Job's current state for breakpoint
        """

        # Note: some state is passed to __init__() thus is not managed
        # as job state but as experiment state in current version
        state = {
            'researcher_id': self._researcher_id,
            'job_id': self._id,
            'model_params_path': self._model_params_file,
            'training_replies': self._save_training_replies(self._training_replies)
        }

        state['model_params_path'] = create_unique_link(
            breakpoint_path, 'aggregated_params_current', '.mpk',
            os.path.join('..', os.path.basename(state["model_params_path"]))
        )

        for round_replies in state['training_replies']:
            for response in round_replies:
                node_params_path = create_unique_file_link(
                    breakpoint_path, response['params_path']
                )
                response['params_path'] = node_params_path

        return state

    def load_state(self, saved_state: Dict[str, Any]) -> None:
        """Load breakpoints state for a Job from a saved state

        Args:
            saved_state: breakpoint content
        """
        # Reload the job and researched ids.
        self._id = saved_state.get('job_id')
        self._researcher_id = saved_state.get('researcher_id')
        # Upload the latest model parameters. This records the filename and url.
        # self.update_parameters(filename=saved_state.get("model_params_path"))

        self._load_and_set_model_params_from_file(saved_state.get("model_params_path"))
        # Reloadthe latest training replies.
        self._training_replies = self._load_training_replies(
            saved_state.get('training_replies', [])
        )

    @staticmethod
    def _save_training_replies(training_replies: Dict[int, Responses]) -> List[List[Dict[str, Any]]]:
        """Extracts a copy of `training_replies` and prepares it for saving in breakpoint

        - strip unwanted fields
        - structure as list/dict, so it can be saved with JSON

        Args:
            training_replies: training replies of already executed rounds of the job

        Returns:
            Extract from `training_replies` formatted for breakpoint
        """
        converted_training_replies = []

        for round_ in training_replies.keys():
            training_reply = copy.deepcopy(training_replies[round_].data())
            # we want to strip some fields for the breakpoint
            for node in training_reply:
                del node['params']
            converted_training_replies.append(training_reply)

        return converted_training_replies

    @staticmethod
    def _load_training_replies(bkpt_training_replies: List[List[dict]]) -> Dict[int, Responses]:
        """Reads training replies from a formatted breakpoint file, and build a job training replies data structure .

        Args:
            bkpt_training_replies: Extract from training replies saved in breakpoint

        Returns:
            Training replies of already executed rounds of the job
        """

        training_replies = {}
        if not bkpt_training_replies:
            logger.warning("No Replies has been found in this breakpoint")
        for round_ in range(len(bkpt_training_replies)):
            loaded_training_reply = Responses(bkpt_training_replies[round_])
            # reload parameters from file params_path
            for node in loaded_training_reply:
                node["params"] = Serializer.load(node["params_path"])["model_weights"]
            training_replies[round_] = loaded_training_reply

        return training_replies

    def check_data_quality(self):
        """Does quality check by comparing datasets that have been found in different nodes. """

        data = self._data.data()
        # If there are more than two nodes ready for the job
        if len(data.keys()) > 1:

            # First check data types are same based on searched tags
            logger.info('Checking data quality of federated datasets...')

            data_types = []  # CSV, Image or default
            shapes = []  # dimensions
            dtypes = []  # variable types for CSV datasets

            # Extract features into arrays for comparison
            for feature in data.values():
                data_types.append(feature["data_type"])
                dtypes.append(feature["dtypes"])
                shapes.append(feature["shape"])

            if len(set(data_types)) > 1:
                raise FedbiomedDataQualityCheckError(
                    f'Different type of datasets has been loaded with same tag: {data_types}'
                )

            if data_types[0] == 'csv':
                if len(set([s[1] for s in shapes])) > 1:
                    raise FedbiomedDataQualityCheckError(
                        f'Number of columns of federated datasets do not match {shapes}.'
                    )

                dtypes_t = list(map(list, zip(*dtypes)))
                for t in dtypes_t:
                    if len(set(t)) > 1:
                        # FIXME: specifying a specific use case (in the condition above) should be avoided
                        raise FedbiomedDataQualityCheckError(
                            f'Variable data types do not match in federated datasets {dtypes}'
                        )

            elif data_types[0] == 'images':
                shapes_t = list(map(list, zip(*[s[2:] for s in shapes])))
                dim_state = True
                for s in shapes_t:
                    if len(set(s)) != 1:
                        dim_state = False

                if not dim_state:
                    logger.error(f'Dimensions of the images in federated datasets \
                                 do not match. Please consider using resize. {shapes} ')

                if len(set([k[1] for k in shapes])) != 1:
                    logger.error(f'Color channels of the images in federated \
                                    datasets do not match. {shapes}')

            # If it is default MNIST dataset pass
            else:
                pass

        pass


class localJob:
    """Represents the entity that manage the training part. LocalJob is the version of Job but applied locally on a
    local dataset (thus not involving any network). It is only used to compare results to a Federated approach, using
    networks.
    """

    def __init__(self, dataset_path: str = None,
                 training_plan_class: str = 'MyTrainingPlan',
                 training_plan_path: str = None,
                 training_args: TrainingArgs = None,
                 model_args: dict = None):

        """
        Constructor of the class

        Args:
            dataset_path : The path where data is stored on local disk.
            training_plan_class: Name of the model class to use for training or model class.
            training_plan_path: path to file containing model code. Defaults to None.
            training_args: contains training parameters: lr, epochs, batch_size...
            model_args: contains output and input feature dimension.
        """

        self._id = str(uuid.uuid4())
        self._repository_args = {}
        self._localjob_training_args = training_args
        self._model_args = model_args
        self._training_args = TrainingArgs(training_args, only_required=False)
        self.dataset_path = dataset_path

        if training_args is not None:
            if training_args.get('test_on_local_updates', False) \
                    or training_args.get('test_on_global_updates', False):
                # if user wants to perform validation, display this message
                logger.warning("Cannot perform validation, not supported for LocalJob")

        # handle case when model is in a file
        if training_plan_path is not None:
            try:
                model_module = os.path.basename(training_plan_path)
                model_module = re.search("(.*)\.py$", model_module).group(1)
                sys.path.insert(0, os.path.dirname(training_plan_path))

                module = importlib.import_module(model_module)
                tr_class = getattr(module, training_plan_class)
                self._training_plan = tr_class()
                sys.path.pop(0)

            except Exception as e:
                e = sys.exc_info()
                logger.critical("Cannot import class " + training_plan_class + " from path " +
                                training_plan_path + " - Error: " + str(e))
                sys.exit(-1)
        else:

            # create/save model instance
            if inspect.isclass(training_plan_class):
                self._training_plan = training_plan_class()
            else:
                self._training_plan = training_plan_class

        self._training_plan.post_init(model_args=self._model_args,
                                      training_args=self._training_args)

    @property
    def training_plan(self):
        return self._training_plan

    @property
    def model(self):
        return self._training_plan.model()

    @property
    def training_args(self):
        return self._localjob_training_args

    @training_args.setter
    def training_args(self, training_args: dict):
        self._localjob_training_args = training_args

    def start_training(self):
        """Sends training task to nodes and waits for the responses"""
        # Run import statements (very unsafely).
        for i in self._training_plan._dependencies:
            exec(i, globals())

        # Run the training routine.
        try:
            self._training_plan.set_dataset_path(self.dataset_path)
            data_manager = self._training_plan.training_data()
            tp_type = self._training_plan.type()
            data_manager.load(tp_type=tp_type)
            train_loader, test_loader = data_manager.split(test_ratio=0)
            self._training_plan.training_data_loader = train_loader
            self._training_plan.testing_data_loader = test_loader
            self._training_plan.training_routine()
        except Exception as exc:
            logger.error("Cannot train model in job: %s", repr(exc))
        # Save the current parameters.
        else:
            try:
                # TODO: should test status code but not yet returned by upload_file
                path = os.path.join(
                    environ["TMP_DIR"], f"local_params_{uuid.uuid4()}.mpk"
                )
                Serializer.dump(self._training_plan.get_model_params(), path)
            except Exception as exc:
                logger.error("Cannot write results: %s", repr(exc))
