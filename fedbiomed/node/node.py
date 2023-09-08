# This file is originally part of Fed-BioMed
# SPDX-License-Identifier: Apache-2.0

'''
Core code of the node component.
'''
from json import decoder
from typing import Optional, Union

import validators

from fedbiomed.common.constants import ComponentType, ErrorNumbers
from fedbiomed.common.exceptions import FedbiomedMessageError, FedbiomedSilentRoundError
from fedbiomed.common.logger import logger
from fedbiomed.common.message import NodeMessages, SecaggDeleteRequest, SecaggRequest, TrainRequest, ErrorMessage
from fedbiomed.common.tasks_queue import TasksQueue

from fedbiomed.node.environ import environ
from fedbiomed.node.history_monitor import HistoryMonitor
from fedbiomed.node.dataset_manager import DatasetManager
from fedbiomed.node.training_plan_security_manager import TrainingPlanSecurityManager
from fedbiomed.node.round import Round
from fedbiomed.node.secagg import SecaggSetup
from fedbiomed.node.secagg_manager import SecaggManager
from fedbiomed.transport.researcher_client_async import ResearcherClient

class Node:
    """Core code of the node component.

    Defines the behaviour of the node, while communicating
    with the researcher through the `Messaging`, parsing messages from the researcher,
    etiher treating them instantly or queuing them,
    executing tasks requested by researcher stored in the queue.
    """

    def __init__(self,
                 dataset_manager: DatasetManager,
                 tp_security_manager: TrainingPlanSecurityManager,
                 node_args: Union[dict, None] = None):
        """Constructor of the class.

        Attributes:
            dataset_manager: `DatasetManager` object for managing the node's datasets.
            tp_security_manager: `TrainingPlanSecurityManager` object managing the node's training plans.
            node_args: Command line arguments for node.
        """

        self.tasks_queue = TasksQueue(environ['MESSAGES_QUEUE_DIR'], environ['TMP_DIR'])
        self._grpc_client = ResearcherClient(
            node_id=environ["ID"],
            on_message=self.on_message,
        )
        self.dataset_manager = dataset_manager
        self.tp_security_manager = tp_security_manager

        self.node_args = node_args

    def add_task(self, task: dict):
        """Adds a task to the pending tasks queue.

        Args:
            task: A `Message` object describing a training task
        """
        self.tasks_queue.add(task)

    def on_message(self, msg: dict, topic: str = None):
        """Handler to be used with `Messaging` class (ie the messager).

        Called when a  message arrives through the `Messaging`.
        It reads and triggers instructions received by node from Researcher,
        mainly:
        - ping requests,
        - train requests (then a new task will be added on node's task queue),
        - search requests (for searching data in node's database).

        Args:
            msg: Incoming message from Researcher.
                Must contain key named `command`, describing the nature
                of the command (ping requests, train requests,
                or search requests).
                Should be formatted as a `Message`.
            topic: Messaging topic name, decision (specially on researcher) may
                be done regarding of the topic. Currently unused.
        """
        # TODO: describe all exceptions defined in this method
        no_print = ["aggregator_args", "aux_vars", "params", "training_plan"]
        msg_print = {key: value for key, value in msg.items() if key not in no_print}
        logger.debug('Message received: ' + str(msg_print))
        try:
            # get the request from the received message (from researcher)
            command = msg['command']
            request = NodeMessages.format_incoming_message(msg).get_dict()
            if command in ['train', 'secagg']:
                # add training task to queue
                self.add_task(request)
            elif command == 'secagg-delete':
                self._task_secagg_delete(NodeMessages.format_incoming_message(msg))
            elif command == 'ping':
                self._grpc_client.send(
                    NodeMessages.format_outgoing_message(
                        {
                            'researcher_id': msg['researcher_id'],
                            'node_id': environ['NODE_ID'],
                            'success': True,
                            'sequence': msg['sequence'],
                            'command': 'pong'
                        }))
            elif command == 'search':
                # Look for databases matching the tags
                databases = self.dataset_manager.search_by_tags(msg['tags'])
                if len(databases) != 0:
                    databases = self.dataset_manager.obfuscate_private_information(databases)
                    # FIXME: what happens if len(database) == 0
                    print(databases)
                    self._grpc_client.send(NodeMessages.format_outgoing_message(
                        {'success': True,
                         'command': 'search',
                         'node_id': environ['NODE_ID'],
                         'researcher_id': msg['researcher_id'],
                         'databases': databases,
                         'count': len(databases)}))
            elif command == 'list':
                # Get list of all datasets
                databases = self.dataset_manager.list_my_data(verbose=False)
                databases = self.dataset_manager.obfuscate_private_information(databases)
                self._grpc_client.send(NodeMessages.format_outgoing_message(
                    {'success': True,
                     'command': 'list',
                     'node_id': environ['NODE_ID'],
                     'researcher_id': msg['researcher_id'],
                     'databases': databases,
                     'count': len(databases),
                     }))
            elif command == 'approval':
                # Ask for training plan approval
                reply = self.tp_security_manager.reply_training_plan_approval_request(request)
                self._grpc_client.send(reply)
            elif command == 'training-plan-status':
                # Check is training plan approved
                reply = self.tp_security_manager.reply_training_plan_status_request(request)
                self._grpc_client.send(reply)
            else:
                raise NotImplementedError('Command not found')

        except NotImplementedError:
            resid = msg.get('researcher_id', 'unknown_researcher_id')
            self.send_error(ErrorNumbers.FB301,
                            extra_msg=f"Command `{command}` is not implemented",
                            researcher_id=resid)
        except KeyError:
            # FIXME: this error could be raised for other missing keys (eg
            # researcher_id, ....)
            resid = msg.get('researcher_id', 'unknown_researcher_id')
            self.send_error(ErrorNumbers.FB301,
                            extra_msg="'command' property was not found",
                            researcher_id=resid)
        except FedbiomedMessageError:  # Message was not properly formatted
            resid = msg.get('researcher_id', 'unknown_researcher_id')
            self.send_error(ErrorNumbers.FB301,
                            extra_msg='Message was not properly formatted',
                            researcher_id=resid)
        except TypeError:  # Message was not serializable
            resid = msg.get('researcher_id', 'unknown_researcher_id')
            self.send_error(ErrorNumbers.FB301,
                            extra_msg='Message was not serializable',
                            researcher_id=resid)

    def _task_secagg_delete(self, msg: SecaggDeleteRequest) -> None:
        """Parse a given secagg delete task message and execute secagg delete task.

        Args:
            msg: `SecaggDeleteRequest` message object to parse
        """

        secagg_id = msg.get_param('secagg_id')

        reply = {
            'researcher_id': msg.get_param('researcher_id'),
            'secagg_id': secagg_id,
            'sequence': msg.get_param('sequence'),
            'command': 'secagg-delete'}

        try:
            secagg_manager = SecaggManager(element=msg.get_param('element'))()
        except Exception as e:
            message = f'{ErrorNumbers.FB321.value}: Can not instantiate SecaggManager object {e}'
            logger.error(message)
            return self.reply({"success": False, "msg": message, **reply})

        try:
            status = secagg_manager.remove(secagg_id=secagg_id,
                                           job_id=msg.get_param('job_id'))
            if status:
                message = 'Delete request is successful'
            else:
                message = f"{ErrorNumbers.FB321.value}: no such secagg context element in node database for " \
                          f"node_id={environ['NODE_ID']} secagg_id={secagg_id}"
        except Exception as e:
            message = f"{ErrorNumbers.FB321.value}: error during secagg delete on node_id={environ['NODE_ID']} " \
                      f'secagg_id={secagg_id}: {e}'
            logger.error(message)
            status = False

        return self.reply({"success": status, "msg": message, **reply})

    def _task_secagg(self, msg: SecaggRequest) -> None:
        """Parse a given secagg setup task message and execute secagg task.

        Args:
            msg: `SecaggRequest` message object to parse
        """
        setup_arguments = {key: value for (key, value) in msg.get_dict().items()}

        try:
            secagg = SecaggSetup(**setup_arguments)()
        except Exception as error_message:
            logger.error(error_message)
            return self.reply({"researcher_id": msg.get_param('researcher_id'),
                               "secagg_id": msg.get_param('secagg_id'),
                               "msg": str(error_message),
                               "sequence": msg.get_param('sequence'),
                               "success": False,
                               "command": "secagg"})

        msg = secagg.setup()
        return self.reply(msg)

    def parser_task_train(self, msg: TrainRequest) -> Union[Round, None]:
        """Parses a given training task message to create a round instance

        Args:
            msg: `TrainRequest` message object to parse

        Returns:
            a `Round` object for the training to perform, or None if no training
        """
        round_ = None
        # msg becomes a TrainRequest object
        hist_monitor = HistoryMonitor(job_id=msg.get_param('job_id'),
                                      researcher_id=msg.get_param('researcher_id'),
                                      send=self._grpc_client.send)

        dataset_id = msg.get_param('dataset_id')
        data = self.dataset_manager.get_by_id(dataset_id)
        if data is None:
            logger.error('Did not found proper data in local datasets '
                         f'on node={environ["NODE_ID"]}')
            self._grpc_client.send(NodeMessages.format_outgoing_message(
                {'command': "error",
                 'node_id': environ['NODE_ID'],
                 'researcher_id': msg.get_param('researcher_id'),
                 'errnum': ErrorNumbers.FB313.name,
                 'extra_msg': "Did not found proper data in local datasets"}
            ))
        else:
            dlp_and_loading_block_metadata = None
            if 'dlp_id' in data:
                dlp_and_loading_block_metadata = self.dataset_manager.get_dlp_by_id(data['dlp_id'])
            round_ = Round(model_kwargs=msg.get_param('model_args') or {},
                            training_kwargs=msg.get_param('training_args') or {},
                            training=msg.get_param('training') or False,
                            dataset=data,
                            params=msg.get_param('params'),
                            job_id=msg.get_param('job_id'),
                            researcher_id=msg.get_param('researcher_id'),
                            history_monitor=hist_monitor,
                            aggregator_args=msg.get_param('aggregator_args') or None,
                            node_args=self.node_args,
                            training_plan=msg.get_param('training_plan'),
                            training_plan_class=msg.get_param('training_plan_class'),
                            round_number=msg.get_param('round'),
                            dlp_and_loading_block_metadata=dlp_and_loading_block_metadata,
                            aux_vars=msg.get_param('aux_vars'),
                            reply_callback=self._grpc_client.send
            )

        return round_

    def task_manager(self):
        """Manages training tasks in the queue.
        """

        while True:
            item = self.tasks_queue.get()
            item_print = {key: value for key, value in item.items() if key != 'aggregator_args'}
            logger.debug(f"[TASKS QUEUE] Task received by task manager: Command: "
                         f"{item['command']} Researcher: {item['researcher_id']} Job: {item.get('job_id')}")
            logger.info("This log is for researcher")
            try:

                item = NodeMessages.format_incoming_message(item)
                command = item.get_param('command')
            except Exception as e:
                # send an error message back to network if something wrong occured
                self._grpc_client.send(
                    NodeMessages.format_outgoing_message(
                        {
                            'command': 'error',
                            'extra_msg': str(e),
                            'node_id': environ['NODE_ID'],
                            'researcher_id': 'NOT_SET',
                            'errnum': ErrorNumbers.FB300.name
                        }
                    )
                )
            else:
                if command == 'train':
                    try:
                        round = self.parser_task_train(item)
                        # once task is out of queue, initiate training rounds
                        if round is not None:
                            # Runs model training and send message using callback
                            round.run_model_training(
                                secagg_arguments={
                                    'secagg_servkey_id': item.get_param('secagg_servkey_id'),
                                    'secagg_biprime_id': item.get_param('secagg_biprime_id'),
                                    'secagg_random': item.get_param('secagg_random'),
                                    'secagg_clipping_range': item.get_param('secagg_clipping_range')
                                }
                            )
                    except FedbiomedSilentRoundError as exp:
                        logger.debug(f"A silent raise caught: {exp}")
                    except Exception as e:
                        # send an error message back to network if something
                        # wrong occured
                        self._grpc_client.send(
                            NodeMessages.format_outgoing_message(
                                {
                                    'command': 'error',
                                    'extra_msg': str(e),
                                    'node_id': environ['NODE_ID'],
                                    'researcher_id': 'NOT_SET',
                                    'errnum': ErrorNumbers.FB300.name
                                }
                            )
                        )
                        logger.debug(f"{ErrorNumbers.FB300}: {e}")
                elif command == 'secagg':
                    self._task_secagg(item)
                else:
                    errmess = f'{ErrorNumbers.FB319.value}: "{command}"'
                    logger.error(errmess)
                    self.send_error(errnum=ErrorNumbers.FB319, extra_msg=errmess)

            self.tasks_queue.task_done()

    def start_messaging(self, block: Optional[bool] = False):
        """Calls the start method of messaging class.

        Args:
            block: Whether messager is blocking (or not). Defaults to False.
        """
        # TODO: remove the `block` quote ? Now non blocking only. Or start in blocking mode ?
        self._grpc_client.start()

    def stop_messaging(self):
        """Shutdown communications with researcher, wait until completion"""
        self._grpc_client.stop()

    def is_connected(self) -> bool:
        """Checks if node is ready for communication with researcher

        Returns:
            True if node is ready, False if node is not ready
        """
        return isinstance(self._grpc_client, ResearcherClient) and self._grpc_client.is_connected()

    def reply(self, msg: dict):
        """Send reply to researcher

        Args:
            msg:

        """

        try:
            reply = NodeMessages.format_outgoing_message(
                {'node_id': environ['ID'],
                 **msg}
            )
        except FedbiomedMessageError as e:
            logger.error(f"{ErrorNumbers.FB601.value}: {e}")
            self.send_error(errnum=ErrorNumbers.FB601, extra_msg=f"{ErrorNumbers.FB601.value}: Can not reply "
                                                                 f"due to incorrect message type {e}.")
        except Exception as e:
            logger.error(f"{ErrorNumbers.FB601.value} Unexpected error while creating node reply message {e}")
            self.send_error(errnum=ErrorNumbers.FB601, extra_msg=f"{ErrorNumbers.FB601.value}: "
                                                                 f"Unexpected error occurred")

        else:
            self._grpc_client.send(reply)



    def send_error(
            self,
            errnum: ErrorNumbers,
            extra_msg: str = "",
            researcher_id: str = "<unknown>"
    ):
        """Sends an error message.

        It is a wrapper of `Messaging.send_error()`.

        Args:
            errnum: Code of the error.
            extra_msg: Additional human readable error message.
            researcher_id: Destination researcher.
        """

        #
        self._grpc_client.send(
            ErrorMessage(
                command='error',
                errnum=errnum.name,
                node_id=environ['NODE_ID'],
                extra_msg=extra_msg,
                researcher_id=researcher_id
            )
        )
