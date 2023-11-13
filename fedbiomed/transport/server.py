
import time
from typing import Callable, Iterable, Any, Coroutine, Dict, Optional, List
import threading

import asyncio
import grpc
from google.protobuf.message import Message as ProtoBufMessage

from fedbiomed.transport.protocols.researcher_pb2 import Empty
import fedbiomed.transport.protocols.researcher_pb2_grpc as researcher_pb2_grpc
from fedbiomed.transport.client import GRPC_CLIENT_CONN_RETRY_TIMEOUT
from fedbiomed.transport.node_agent import AgentStore, NodeActiveStatus, NodeAgent

from fedbiomed.common.constants import ErrorNumbers
from fedbiomed.common.exceptions import FedbiomedCommunicationError
from fedbiomed.common.logger import logger
from fedbiomed.common.serializer import Serializer
from fedbiomed.common.message import Message, TaskResponse, TaskRequest, FeedbackMessage
from fedbiomed.common.constants import MessageType, MAX_MESSAGE_BYTES_LENGTH


# timeout in seconds for server to establish connections with nodes and initialize
GPRC_SERVER_SETUP_TIMEOUT = GRPC_CLIENT_CONN_RETRY_TIMEOUT + 1
MAX_GRPC_SERVER_SETUP_TIMEOUT = 20


class ResearcherServicer(researcher_pb2_grpc.ResearcherServiceServicer):
    """RPC Servicer """

    def __init__(
            self,
            agent_store: AgentStore,
            on_message: Callable
    ) -> None:
        """Constructor of gRPC researcher servicer

        Args:
            agent_store: The class that stores node agents
            on_message: Callback function to execute once a message received from the nodes
        """
        super().__init__()
        self._agent_store = agent_store
        self._on_message = on_message


    async def GetTaskUnary(
            self,
            request: ProtoBufMessage,
            context: grpc.aio.ServicerContext
    ) -> None:
        """Gets unary RPC request and return stream of response

        Args:
            request: RPC request
            context: RPC peer context
        """

        task_request = TaskRequest.from_proto(request).get_dict()
        logger.debug(f"Node: {task_request.get('node')} polling for the tasks")

        node_agent = await self._agent_store.retrieve(
            node_id=task_request["node"],
            context=context
        )
        
        # Update node active status as active
        await node_agent.set_active()

        task = await node_agent.get_task()

        # Choice: be simple, mark task as de-queued as soon as retrieved
        node_agent.task_done()

        task = Serializer.dumps(task.get_dict())

        chunk_range = range(0, len(task), MAX_MESSAGE_BYTES_LENGTH)
        for start, iter_ in zip(chunk_range, range(1, len(chunk_range) + 1)):
            stop = start + MAX_MESSAGE_BYTES_LENGTH

            yield TaskResponse(
                size=len(chunk_range),
                iteration=iter_,
                bytes_=task[start:stop]
            ).to_proto()


    async def ReplyTask(
            self,
            request_iterator: Iterable[ProtoBufMessage],
            context: grpc.aio.ServicerContext
    ) -> None:
        """Gets stream replies from the nodes

        Args:
            request_iterator: Iterator for streaming
            context: Request service context
        """
        
        reply = bytes()
        async for answer in request_iterator:
            reply += answer.bytes_
            if answer.size != answer.iteration:
                continue
            else:
                # Deserialize message
                message = Serializer.loads(reply)
                
                # New implementation
                node = await self._agent_store.get(message["node_id"])
                await node.on_reply(message)

                # self._on_message(message, MessageType.REPLY)
                reply = bytes()

        return Empty()


    async def Feedback(
            self,
            request: ProtoBufMessage,
            unused_context: grpc.aio.ServicerContext
    ) -> None:
        """Executed for Feedback request received from the nodes

        Args:
            request: Feedback message
            unused_context: Request service context
        """

        # Get the type of Feedback | log or scalar
        one_of = request.WhichOneof("feedback_type")
        feedback = FeedbackMessage.from_proto(request)

        # Execute on message assigned by the researcher.requests modules
        self._on_message(feedback.get_param(one_of), MessageType.convert(one_of))

        return Empty()


class _GrpcAsyncServer:
    """GRPC Server class.

    All the methods of this class are awaitable, except the constructor.
    """
    def __init__(
            self,
            host: str,
            port: str,
            on_message: Callable,
            debug: bool = False,
    ) -> None:
        """Class constructor

        Args:
            host: server DNS name or IP address
            port: server TCP port
            on_message: Callback function to execute once a message received from the nodes
            debug: Activate debug mode for gRPC asyncio
        Raises:
            FedbiomedCommunicationError: bad argument type
        """
 
        # inform all threads whether server is started
        self._is_started = threading.Event()

        self._host = host
        self._port = port

        self._server = None
        self._debug = debug
        self._on_message = on_message
        self._loop = None
        self._agent_store : Optional[AgentStore] = None


    async def start(self):
        """Starts gRPC server"""

        self._server = grpc.aio.server(
            # futures.ThreadPoolExecutor(max_workers=10),
            options=[
                ("grpc.max_send_message_length", 100 * 1024 * 1024),
                ("grpc.max_receive_message_length", 100 * 1024 * 1024),
            ])

        self._loop = asyncio.get_running_loop()
        self._agent_store = AgentStore(loop=self._loop)

        researcher_pb2_grpc.add_ResearcherServiceServicer_to_server(
            ResearcherServicer(
                agent_store=self._agent_store,
                on_message=self._on_message),
            server=self._server
        )

        self._server.add_insecure_port(self._host + ':' + str(self._port))

        # Starts async gRPC server
        await self._server.start()

        self._is_started.set()
        try:
            if self._debug:
                logger.debug("Waiting for termination")
            await self._server.wait_for_termination()
        finally:
            if self._debug:
                logger.debug("Done starting the server")


    async def send(self, message: Message, node_id: str) -> None:
        """Broadcasts given message to all active clients.

        Args:
            message: Message to broadcast
        """
         
        agent = await self._agent_store.get(node_id)
        
        if not agent:
            logger.info(f"Node {node_id} is not registered on server. Discard message.")
            return

        await agent.send(message)


    async def broadcast(self, message: Message) -> None:
        """Broadcasts given message to all active clients.

        Args:
            message: Message to broadcast
        """

        agents = await self._agent_store.get_all()
        for _, agent in agents.items():
            await agent.send(message)

    async def get_node(self, node_id: str) -> Optional[NodeAgent]:
        """Returns given node

        Args:
            node_id: ID of node to retrieve

        Returns:
            A node agent
        """

        return await self._agent_store.get(node_id)

    async def get_all_nodes(self) -> List[NodeAgent]:
        """Returns all known nodes

        Returns:
            A list of node agents
        """

        agents = await self._agent_store.get_all()

        return [node for node in agents.values()]



class GrpcServer(_GrpcAsyncServer):
    """Grpc server implementation to be used by threads

    This class extends async implementation of gRPC server to be able to
    call async methods from different thread. Currently, it is used by
    [fedbiomed.researcher.requests.Requests][`Requests`] class that is
    instantiated in the main thread

    Attributes:
        _thread: background thread of gRPC server
    """

    _thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        """Runs asyncio application"""
        try:
            asyncio.run(super().start())
        except Exception as e:
            logger.error(f"Researcher gRPC server has stopped. Please try to restart: {e}")

    def start(self) -> None:
        """Stats async GrpcServer """

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        # FIXME: This implementation assumes that nodes will be able connect and server complete setup with this delay
        logger.info("Starting researcher service...")

        
        logger.info(f'Waiting {GPRC_SERVER_SETUP_TIMEOUT}s for nodes to connect...')
        time.sleep(GPRC_SERVER_SETUP_TIMEOUT)

        sleep_ = 0
        while len(self.get_all_nodes()) == 0:
            
            if sleep_ == 0:
                logger.info(f"No nodes found, server will wait {MAX_GRPC_SERVER_SETUP_TIMEOUT - GPRC_SERVER_SETUP_TIMEOUT} " 
                            "more seconds until a node creates connection.")
            
            if sleep_ > MAX_GRPC_SERVER_SETUP_TIMEOUT - GPRC_SERVER_SETUP_TIMEOUT:
                if len(self.get_all_nodes()) == 0:
                    logger.warning("Server has not received connection from any remote nodes in " 
                                   f"MAX_GRPC_SERVER_SETUP_TIMEOUT: {MAX_GRPC_SERVER_SETUP_TIMEOUT} "
                                   "This may effect the request created right after the server initialization. " 
                                   "However, server will keep running in the background so you can retry the "
                                   "operations for sending requests to remote nodes until one receives.")
                break

            time.sleep(1)
            sleep_ += 1
        

    def send(self, message: Message, node_id: str) -> None:
        """Send message to a specific node.

        Args:
            message: Message to send
            node_id: Destination node unique ID

        Raises:
            FedbiomedCommunicationError: bad argument type
            FedbiomedCommunicationError: server is not started
        """
        if not isinstance(message, Message):
            raise FedbiomedCommunicationError(
                f"{ErrorNumbers.FB628}: bad argument type for message, expected `Message`, got `{type(message)}`")

        if not self._is_started.is_set():
            raise FedbiomedCommunicationError(f"{ErrorNumbers.FB628.value}: Communication client is not initialized.")

        self._run_threadsafe(super().send(message, node_id))

    def broadcast(self, message: Message) -> None:
        """Broadcast message to all known and reachable nodes

        Args:
            message: Message to broadcast

        Raises:
            FedbiomedCommunicationError: bad argument type
            FedbiomedCommunicationError: server is not started
        """
        if not isinstance(message, Message):
            raise FedbiomedCommunicationError(
                f"{ErrorNumbers.FB628}: bad argument type for message, expected `Message`, got `{type(message)}`")

        if not self._is_started.is_set():
            raise FedbiomedCommunicationError(f"{ErrorNumbers.FB628}: Communication client is not initialized.")

        self._run_threadsafe(super().broadcast(message))

    # TODO: Currently unused

    def get_all_nodes(self) -> List[NodeAgent]:
        """Returns all known nodes

        Returns:
            A list of node agents

        Raises:
            FedbiomedCommunicationError: server is not started
        """
        if not self._is_started.is_set():
            raise FedbiomedCommunicationError(f"{ErrorNumbers.FB628}: Communication client is not initialized.")

        return self._run_threadsafe(super().get_all_nodes())

    def get_node(self, node_id) -> Optional[NodeAgent]:
        """Returns given node

        Args:
            node_id: ID of node to retrieve

        Returns:
            A node agent

        Raises:
            FedbiomedCommunicationError: server is not started
        """
        if not self._is_started.is_set():
            raise FedbiomedCommunicationError(f"{ErrorNumbers.FB628}: Communication client is not initialized.")

        return self._run_threadsafe(super().get_node(node_id))

    # TODO: Currently unused

    def is_alive(self) -> bool:
        """Checks if the thread running gRPC server still alive

        Returns:
            gRPC server running status

        Raises:
            FedbiomedCommunicationError: server is not started
        """
        if not self._is_started.is_set():
            raise FedbiomedCommunicationError(f"{ErrorNumbers.FB628}: Communication client is not initialized.")

        # TODO: more tests about gRPC server and task status ?
        return False if not isinstance(self._thread, threading.Thread) else self._thread.is_alive()

    def _run_threadsafe(self, coroutine: Coroutine) -> Any:
        """Runs given coroutine threadsafe

        Args:
            coroutine: Awaitable function to be executed as threadsafe

        Returns:
            Coroutine return value.
        """

        future = asyncio.run_coroutine_threadsafe(
            coroutine, self._loop
        )


        return future.result()
