import unittest
from unittest.mock import MagicMock, patch

#############################################################
# Import ResearcherTestCase before importing any FedBioMed Module
from testsupport.base_case import ResearcherTestCase
from testsupport.base_mocks import MockRequestModule
#############################################################

import fedbiomed
from fedbiomed.common.constants import __breakpoints_version__
from fedbiomed.common.training_args import TrainingArgs
from fedbiomed.researcher.environ import environ
from fedbiomed.researcher.federated_workflows import FederatedWorkflow
from fedbiomed.researcher.secagg import SecureAggregation


class TestFederatedWorkflow(ResearcherTestCase, MockRequestModule):

    def setUp(self):
        MockRequestModule.setUp(self, module="fedbiomed.researcher.federated_workflows._federated_workflow.Requests")
        super().setUp()
        self.abstract_methods_patcher = patch.multiple(FederatedWorkflow, __abstractmethods__=set())
        self.abstract_methods_patcher.start()

    def tearDown(self):
        super().tearDown()
        self.abstract_methods_patcher.stop()

    def test_federated_workflow_01_initialization(self):
        """Test initialization of federated workflow, only cases where correct parameters are provided"""
        # FederatedWorkflow must be default-constructible
        exp = FederatedWorkflow()
        # FederatedWorkflow has a lot of setters
        # tags
        self.assertIsNone(exp.tags())  # by default, tags set to None
        exp.set_tags('just-a-str')
        self.assertEqual(exp.tags(), ['just-a-str'])
        exp.set_tags(None)
        self.assertIsNone(exp.tags())
        exp.set_tags(['first', 'second'])
        self.assertEqual(exp.tags(), ['first', 'second'])
        # nodes
        exp = FederatedWorkflow()
        self.assertIsNone(exp.nodes())  # by default, nodes set to None
        exp.set_nodes(None)
        self.assertIsNone(exp.nodes())
        exp.set_nodes(['first', 'second'])
        self.assertEqual(exp.nodes(), ['first', 'second'])
        # training_data
        exp = FederatedWorkflow()
        self.assertIsNone(exp.training_data())  # by default, training data is initialized to something
        exp.set_training_data(None, from_tags=False)
        self.assertIsNone(exp.training_data())
        self.fake_search_reply = {'node1': [{'my-metadata': 'is-the-best', 'tags': ['some-tag']}]}
        self.mock_requests.return_value.search.return_value = self.fake_search_reply
        exp.set_tags('just-a-str')
        exp.set_training_data(None, from_tags=True)
        self.assertDictEqual(exp.training_data().data(),
                             {'node1': {'my-metadata': 'is-the-best', 'tags': ['some-tag']}})
        _training_data = MagicMock(spec=fedbiomed.researcher.datasets.FederatedDataSet)
        exp.set_training_data(_training_data)
        self.assertEqual(exp.training_data(), _training_data)
        # experimentation_folder
        exp = FederatedWorkflow()
        self.assertIsNotNone(exp.experimentation_folder())  # by default, exp folder is initialized to something
        with patch('fedbiomed.researcher.federated_workflows._federated_workflow.create_exp_folder') as mock_exp_folder_creat:
            exp.set_experimentation_folder()
            mock_exp_folder_creat.assert_called_once_with()
            self.assertIsNotNone(exp.experimentation_folder())
            old_folder = exp.experimentation_folder()
            mock_exp_folder_creat.reset_mock()
            exp.set_experimentation_folder('new-name')
            mock_exp_folder_creat.assert_called_once_with('new-name')
        # training args
        exp = FederatedWorkflow()
        self.assertIsNotNone(exp.training_args())
        # by default, training_args is set to TrainingArgs(None), which is populated with several default values
        self.assertTrue(isinstance(exp.training_args(), dict))
        self.assertTrue(len(exp.training_args()) >= 1)
        exp.set_training_args({'num_updates': 42})
        self.assertTrue(exp.training_args()['num_updates'] == 42)
        exp.set_training_args(TrainingArgs({'epochs': 42}))
        self.assertTrue(exp.training_args()['epochs'] == 42)
        # secagg
        exp = FederatedWorkflow()
        self.assertTrue(isinstance(exp.secagg, SecureAggregation))  # set to inactive SecureAggregation
        self.assertFalse(exp.secagg.active)
        exp.set_secagg(True)
        self.assertTrue(isinstance(exp.secagg, SecureAggregation))  # set to inactive SecureAggregation
        self.assertTrue(exp.secagg.active)
        _secagg = MagicMock(spec=fedbiomed.researcher.secagg.SecureAggregation)
        exp.set_secagg(_secagg)
        self.assertEqual(exp.secagg, _secagg)

        # Federated Workflow can also be constructed by providing parameters to the constructor
        # test case where tags are None but we are setting training data
        _training_data.node_ids.return_value = ['alice', 'bob']  # make sure that nodes can be correctly inferred
        exp = FederatedWorkflow(
            nodes=['alice', 'bob'],
            training_data=_training_data,
            training_args={'num_updates': 1},
            secagg=True,
            save_breakpoints=True
        )
        self.assertListEqual(exp.nodes(), ['alice', 'bob'])
        self.assertEqual(exp.training_data(), _training_data)
        self.assertDictEqual(exp.training_args(), TrainingArgs({'num_updates': 1}, only_required=False).dict())
        self.assertTrue(isinstance(exp.secagg, SecureAggregation))
        self.assertTrue(exp.secagg.active)
        self.assertTrue(exp.save_breakpoints())
        # test special cases regarding training data:
        # a. when tags are provided but training data is not provided, build training data from tags
        self.fake_search_reply = {'node1': [{'my-metadata': 'is-the-best', 'tags': ['some-tags']}]}
        self.mock_requests.return_value.search.return_value = self.fake_search_reply
        exp = FederatedWorkflow(
            tags='some-tags'
        )
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertDictEqual(exp.training_data().data(), self.fake_search_reply)
        # b. when tags, nodes and training data are provided, the latter takes precedence and tags, nodes are overridden
        exp = FederatedWorkflow(
            tags='some-tags',
            nodes=['wrong', 'nodes'],
            training_data=_training_data
        )
        self.assertIsNone(exp.tags())  # in this case, tags are set to None
        self.assertListEqual(exp.nodes(), ['alice', 'bob'])
        self.assertEqual(exp.training_data(), _training_data)


    def test_federated_workflow_02_consistency_fds_tags_nodes(self):
        """"""
        self.fake_search_reply = {'node1': [{'my-metadata': 'is-the-best', 'tags': ['some-tags']}]}
        self.mock_requests.return_value.search.return_value = self.fake_search_reply
        exp = FederatedWorkflow()
        # setting tags when training data is None -> simply set tags
        exp.set_tags(['some-tags'])
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertIsNone(exp.training_data())
        self.assertIsNone(exp.nodes())
        # setting nodes when training data is None -> simply set nodes
        exp.set_nodes(['alice'])
        self.assertListEqual(exp.nodes(), ['alice'])
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertIsNone(exp.training_data())
        # setting training data from tags, when tags or nodes is not None -> reset only nodes
        exp.set_training_data(None, from_tags=True)
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertListEqual(exp.nodes(), ['node1'])
        self.assertDictEqual(exp.training_data().data(), self.fake_search_reply)
        # resetting tags to None -> simply set it to None
        exp.set_tags(None)
        self.assertIsNone(exp.tags())
        self.assertListEqual(exp.nodes(), ['node1'])
        self.assertDictEqual(exp.training_data().data(), self.fake_search_reply)
        # setting training data from tags, when tags is None -> raise error
        with self.assertRaises(SystemExit):
            exp.set_training_data(None, from_tags=True)
        # resetting training data to None -> set it to None
        exp.set_training_data(None)
        self.assertIsNone(exp.tags())
        self.assertIsNone(exp.training_data())
        self.assertListEqual(exp.nodes(), ['node1'])
        # set nodes when training data is None -> simply set nodes
        exp.set_nodes(['alice'])
        self.assertIsNone(exp.tags())
        self.assertIsNone(exp.training_data())
        self.assertListEqual(exp.nodes(), ['alice'])
        # set training data with an object that's consistent with the current nodes
        _federated_dataset = MagicMock(spec=fedbiomed.researcher.datasets.FederatedDataSet)
        _federated_dataset.node_ids.return_value = ['alice']
        exp.set_training_data(_federated_dataset)
        self.assertIsNone(exp.tags())
        self.assertListEqual(exp.nodes(), ['alice'])
        self.assertEqual(exp.training_data(), _federated_dataset)
        # set training data with an object that's NOT consistent with the current nodes
        _federated_dataset.node_ids.return_value = ['bob']
        exp.set_training_data(_federated_dataset)
        self.assertIsNone(exp.tags())
        self.assertListEqual(exp.nodes(), ['bob'])
        self.assertEqual(exp.training_data(), _federated_dataset)
        # set tags when training data is not None -> reset training data based on current nodes
        self.fake_search_reply = {}  # no nodes named bob
        self.mock_requests.return_value.search.return_value = self.fake_search_reply
        exp.set_tags(['some-tags'])
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertIsNone(exp.nodes())
        self.assertDictEqual(exp.training_data().data(), self.fake_search_reply)
        # reset nodes
        self.fake_search_reply = {'node1': [{'my-metadata': 'is-the-best', 'tags': ['some-tags']}]}
        self.mock_requests.return_value.search.return_value = self.fake_search_reply
        exp.set_nodes(['node1'])
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertListEqual(exp.nodes(), ['node1'])
        self.assertDictEqual(exp.training_data().data(), self.fake_search_reply)


    def test_federated_workflow_03_secagg_setup(self):
        """Test secagg setup functionality and side-effects"""
        with patch('fedbiomed.researcher.federated_workflows._federated_workflow.SecureAggregation') as mock_secagg:
            # normal call
            _secagg = MagicMock(spec=SecureAggregation)
            _secagg.active = True
            _secagg.train_arguments.return_value = {'secagg': 'arguments'}
            mock_secagg.return_value = _secagg
            exp = FederatedWorkflow(secagg=True)
            secagg_args = exp.secagg_setup(['sampled-nodes'])
            _secagg.setup.assert_called_once_with(parties=[environ["ID"], 'sampled-nodes'],
                                                  job_id=exp.id)
            self.assertDictEqual(secagg_args, {'secagg': 'arguments'})
            # call with empty nodes list
            _secagg.setup.reset_mock()
            _secagg.train_arguments.return_value = {'secagg': 'arguments'}
            mock_secagg.return_value = _secagg
            exp = FederatedWorkflow(secagg=True)
            secagg_args = exp.secagg_setup([])
            _secagg.setup.assert_called_once_with(parties=[environ["ID"]],
                                                  job_id=exp.id)
            self.assertDictEqual(secagg_args, {'secagg': 'arguments'})
            # deactivate secagg
            _secagg.setup.reset_mock()
            _secagg.active = False
            mock_secagg.return_value = _secagg
            exp = FederatedWorkflow(secagg=True)
            secagg_args = exp.secagg_setup(['sampled-nodes'])
            self.assertEqual(_secagg.setup.call_count, 0)
            self.assertDictEqual(secagg_args, {})
        # do not mock whole secagg module, and test that calling setup_secagg when secagg is inactive is a
        # noop that returns an empty dict
        with patch('fedbiomed.researcher.federated_workflows._federated_workflow.SecureAggregation.setup',
                   ) as mock_secagg_setup:
            exp = FederatedWorkflow(secagg=False)
            secagg_args = exp.secagg_setup([])
            self.assertEqual(mock_secagg_setup.call_count, 0)
            self.assertDictEqual(secagg_args, {})

    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.open')
    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.json.dump')
    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.choose_bkpt_file',
           return_value=('/bkpt-path', 'bkpt-folder'))
    def test_federated_workflow_04_breakpoint(self,
                                              mock_bkpt_file,
                                              mock_json_dump,
                                              mock_open
                                              ):
        # define attributes that will be saved in breakpoint
        _training_data = MagicMock(spec=fedbiomed.researcher.datasets.FederatedDataSet)
        _training_data.data.return_value = {'training': 'data'}
        exp = FederatedWorkflow(
            training_args={'num_updates': 42},
            training_data=_training_data,
        )
        exp.breakpoint(state={}, bkpt_number=1)
        # This also validates the breakpoint scheme: if this fails, please consider updating the breakpoints version
        mock_json_dump.assert_called_once_with(
            {
                'id': exp.id,
                'breakpoint_version': str(__breakpoints_version__),
                'training_data': {'training': 'data'},
                'training_args': TrainingArgs({'num_updates': 42}, only_required=False).dict(),
                'experimentation_folder': exp.experimentation_folder(),
                'tags': exp.tags(),
                'nodes': exp.nodes(),
                'secagg': exp.secagg.save_state_breakpoint(),
                'node_state': exp._node_state_agent.save_state_breakpoint()
            },
            mock_open.return_value.__enter__.return_value
        )

    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.open')
    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.json.load')
    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.find_breakpoint_path',
           return_value=('/bkpt-path', 'bkpt-folder'))
    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.SecureAggregation.load_state_breakpoint')
    @patch('fedbiomed.researcher.federated_workflows._federated_workflow.NodeStateAgent.load_state_breakpoint')
    def test_federated_workflow_05_load_breakpoint(self,
                                                   mock_node_state_load,
                                                   mock_secagg_load,
                                                   mock_bkpt_file,
                                                   mock_json_load,
                                                   mock_open
                                                   ):
        mock_secagg_load.return_value = MagicMock(spec=SecureAggregation)
        mock_node_state_load.return_value = MagicMock(spec=fedbiomed.researcher.node_state_agent.NodeStateAgent)
        mock_json_load.return_value = {
                'id': 'exp-id',
                'breakpoint_version': str(__breakpoints_version__),
                'training_data': {'node1': [{'training': 'data', 'tags': 'some-tags'}]},
                'training_args': TrainingArgs({'num_updates': 42}, only_required=False).dict(),
                'experimentation_folder': 'some-folder',
                'tags': ['some-tags'],
                'nodes': ['node1'],
                'secagg': {'secagg': 'bkpt'},
                'node_state': {'node_state': 'bkpt'},
                'downstream': 'bkpt'
            }

        exp, saved_state = FederatedWorkflow.load_breakpoint()
        self.assertEqual(exp.id, 'exp-id')
        self.assertDictEqual(exp.training_args(), TrainingArgs({'num_updates': 42}, only_required=False).dict())
        self.assertEqual(exp.training_data().data(), {'node1': {'training': 'data', 'tags': 'some-tags'}})
        self.assertListEqual(exp.nodes(), ['node1'])
        self.assertListEqual(exp.tags(), ['some-tags'])
        self.assertEqual(saved_state['id'], 'exp-id')
        self.assertDictEqual(saved_state['training_args'], TrainingArgs({'num_updates': 42}, only_required=False).dict())
        self.assertEqual(saved_state['training_data'], {'node1': {'training': 'data', 'tags': 'some-tags'}})
        self.assertListEqual(saved_state['nodes'], ['node1'])
        self.assertListEqual(saved_state['tags'], ['some-tags'])
        self.assertDictEqual(saved_state['secagg'], {'secagg': 'bkpt'})
        self.assertDictEqual(saved_state['node_state'], {'node_state': 'bkpt'})
        self.assertEqual(saved_state['downstream'], 'bkpt')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
