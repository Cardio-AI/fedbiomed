import copy
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from fedbiomed.common.constants import NODE_STATE_PREFIX, __node_state_version__
from fedbiomed.common.exceptions import FedbiomedNodeStateManagerError
from fedbiomed.node.node_state_manager import NodeStateManager, NodeStateFileName
from testsupport.fake_uuid import FakeUuid
import testsupport.fake_researcher_environ  ## noqa (remove flake8 false warning)


class TestNodeStateManager(unittest.TestCase):
    
    
    def setUp(self) -> None:
        self.query_patcher = patch('fedbiomed.node.node_state_manager.Query')
        self.table_patcher = patch('fedbiomed.node.node_state_manager.TinyDB')

        self.query_mock = self.query_patcher.start()
        self.table_mock = self.table_patcher.start()
        self.test_nsm = NodeStateManager("path/to/db")
    
        self.fake_declearn_optimizer_state = {'config': {'lrate': 0.2, 'w_decay': 0.0, 'regularizers': [], 'modules': [
            ('adam', {'beta_1': 0.9, 'beta_2': 0.99, 'amsgrad': False, 'eps': 1e-07})]
                                 }, 'states': {'modules': [
                                     ('adam', {'steps': 0, 'vmax': None, 'momentum': {'state': 0.0}, 'velocity': {'state': 0.0}})]}}

    def tearDown(self) -> None:
        self.query_patcher.stop()
        self.table_patcher.stop()
    
    def test_node_state_manager_1_fail_to_build(self):
        query_patcher = patch('fedbiomed.node.node_state_manager.Query')
        table_mock = MagicMock(table=MagicMock(side_effect=NameError("this is a test")))
        db_patcher = patch('fedbiomed.node.node_state_manager.TinyDB', return_value = table_mock)
        table_patcher = patch('fedbiomed.node.node_state_manager.TinyDB.table')
        
        
        query_patcher.start()
        table_patcher.start()
        db_patcher.start()
        
        with self.assertRaises(FedbiomedNodeStateManagerError):
            nsm = NodeStateManager('path/to/db')
        
        query_patcher.stop()
        table_patcher.stop()
        db_patcher.stop()
    

    @patch('fedbiomed.node.node_state_manager.raise_for_version_compatibility')
    def test_node_state_manager_2_get(self, raise_for_compatibility_patch):
        
        #self.query_patcher = patch('fedbiomed.node.node_state_manager.Query')
        job_id , state_id = 'job_id' , 'state_id'
        self.query_mock.job_id = MagicMock(return_value=job_id)
        self.query_mock.state_id = MagicMock(return_value=state_id)
        self.table_mock.return_value.table.return_value.get.return_value = {
            "version_node_id" : '1.2.3',
            'state_id': state_id,
            'job_id': job_id,
            'state': {}
        }
        test_nsm = self.test_nsm
        res = test_nsm.get(job_id, state_id)

        self.table_mock.return_value.table.return_value.get.assert_called_once_with(
            (self.query_mock.job_id == job_id) & (self.query_mock.state_id == state_id)
        )
        self.assertIsInstance(res, dict)
        raise_for_compatibility_patch.assert_called_once()
    
    @patch('fedbiomed.node.node_state_manager.NodeStateManager._load_state') 
    def test_node_state_manager_3_get_error(self, private_load_state_mock):
        private_load_state_mock.return_value = None
        job_id , state_id = 'job_id' , 'state_id'
        with self.assertRaises(FedbiomedNodeStateManagerError):
            self.test_nsm.get(job_id, state_id) 
        
    @patch('uuid.uuid4', autospec=True)
    def test_node_state_manager_4_add(self, uuid_patch):
        
        job_id = 'job_id'
        
        # TODO: add states that are framework native (here we are using format that is very similar to Declearn optimizers)
        
        
        expected_state_id = NODE_STATE_PREFIX + str(FakeUuid.VALUE)
        header = {
            "version_node_id": str(__node_state_version__),
            "state_id": expected_state_id,
            "job_id": job_id
        }
        
        
        uuid_patch.return_value = FakeUuid()
        
        self.query_mock.return_value.state_id = expected_state_id

        res = self.test_nsm.add(job_id, self.fake_declearn_optimizer_state)
        
        # checks
        expected_state = copy.deepcopy(self.fake_declearn_optimizer_state)
        expected_state.update(header)
        self.assertEqual(res, expected_state_id)
        self.table_mock.return_value.table.return_value.upsert.assert_called_once_with(
            expected_state, True
        )
      
    @patch('uuid.uuid4', autospec=True)  
    def test_node_state_manager_5_add_saving_failure(self, uuid_patch):
        # test case where private `_save_state` method fails
        uuid_patch.return_value = FakeUuid()
        self.table_mock.return_value.table.return_value.upsert = MagicMock(
            side_effect=RuntimeError("this error is raised for the sake of testing")
            )

        with self.assertRaises(FedbiomedNodeStateManagerError):
            self.test_nsm.add(job_id='job_id', state=self.fake_declearn_optimizer_state)


    def test_node_state_manager_6_initialize_node_state_manager(self):
        
        fake_var_dir, fake_node_id = 'fake/var/dir', 'fake_node_id_xxx'
        side_effect_env = lambda x: {"VAR_DIR": fake_var_dir, "NODE_ID": fake_node_id}.get(x)
        previous_state_id = 'previous_state_id'
        with (patch('os.makedirs') as os_mkdirs_mock,
              patch('fedbiomed.node.node_state_manager.environ') as node_environ_patch):
            node_environ_patch.__getitem__.side_effect = side_effect_env
            self.test_nsm.initialize(previous_state_id=previous_state_id)
            
            expected_path = os.path.join(fake_var_dir, "node_state_%s" % fake_node_id)
            os_mkdirs_mock.assert_called_once_with(expected_path,
                                                   exist_ok=True)
            
        self.assertEqual(previous_state_id, self.test_nsm.previous_state_id)
        self.assertEqual(self.test_nsm.get_node_state_base_dir(), expected_path)
    
    def test_node_state_manager_7_initialize_node_state_manager_fails(self):
        with patch('os.makedirs') as os_mkdirs_mock:
            os_mkdirs_mock.side_effect = PermissionError("raised for the sake of testing")
            with self.assertRaises(FedbiomedNodeStateManagerError):
                self.test_nsm.initialize()

    @patch('uuid.uuid4', autospec=True)  
    def test_node_state_manager_8_generate_folder_and_create_file_name(self, uuid_patch):
        # testing values for initializing NodeStateManager folder structure
        fake_var_dir, fake_node_id = 'fake/var/dir', 'fake_node_id_xxx'
        side_effect_env = lambda x: {"VAR_DIR": fake_var_dir, "NODE_ID": fake_node_id}.get(x)
        
        # testing values for initializing folders for a given job and round
        file_name = "file_name_for_elem_%s_%s"
        job_id, round_nb, opt_file_name = 'job_id', 4321, MagicMock(spec=NodeStateFileName, 
                                                                    value=file_name)
        uuid_patch.return_value = FakeUuid()
        state_id = FakeUuid.VALUE
        with (patch('os.makedirs') as os_mkdirs_mock,
              patch('fedbiomed.node.node_state_manager.environ') as node_environ_patch):
            node_environ_patch.__getitem__.side_effect = side_effect_env
            self.test_nsm.initialize()
            
            os_mkdirs_mock.reset_mock()
            
            res = self.test_nsm.generate_folder_and_create_file_name(job_id, round_nb, opt_file_name)
            
            # checks
            os_mkdirs_mock.assert_called_once_with(
                os.path.join(self.test_nsm.get_node_state_base_dir(),
                             'job_id_%s' % job_id),
                exist_ok=True
                )

        self.assertEqual(res, os.path.join('fake/var/dir','node_state_fake_node_id_xxx', 'job_id_%s' % job_id,
                                            file_name % (str(round_nb), "node_state_%s" % state_id)))
            
            
    #FIXME; should state_id be private?
    
class TestNodeStateFileName(unittest.TestCase):
    def test_node_state_file_name_1_correct_format_entries(self):
        # here we test that all entries of NodeStateFIleName enum class respect convention

        for entry_value in NodeStateFileName.list():
            try:
                entry_value % ('string_1', 'string_2')
            except TypeError as te:
                self.assertTrue(False, f"error in NodeStateFileName, entry {entry_value} doesnot respect formatting convention"
                                f" details : {te}") 


if __name__ == '__main__':  # pragma: no cover
    unittest.main()