import copy
import random
from typing import Any, Dict, List
import unittest
from unittest.mock import MagicMock, patch, Mock
from fedbiomed.common.exceptions import FedbiomedOptimizerError

import numpy as np
import torch.nn as nn
import torch
from sklearn.base import BaseEstimator
from sklearn.linear_model import SGDClassifier, SGDRegressor
from declearn.optimizer import Optimizer as DecOptimizer
from declearn.optimizer.modules import (
        ScaffoldServerModule, ScaffoldClientModule, GaussianNoiseModule,
        YogiMomentumModule, L2Clipping, AdaGradModule, YogiModule)
from declearn.optimizer.regularizers import FedProxRegularizer, LassoRegularizer, RidgeRegularizer
from declearn.model.torch import TorchVector
from declearn.model.sklearn import NumpyVector

from fedbiomed.common.optimizers.generic_optimizers import NativeTorchOptimizer, DeclearnOptimizer
from fedbiomed.common.models import SkLearnModel, Model, TorchModel, BaseSkLearnModel
from fedbiomed.common.optimizers.optimizer import Optimizer as FedOptimizer


class TestDeclearnOptimizer(unittest.TestCase):

    def setUp(self) -> None:
        self._optim_wrapper = DeclearnOptimizer

        self._torch_model = nn.Linear(4,2)
        self._zero_model = copy.deepcopy(self._torch_model)
        # setting all coefficients of `zero_model` to 0
        for p in self._zero_model.parameters():
            p.data.fill_(0)
        self._sklearn_model_wrappers = (SkLearnModel(SGDClassifier),
                                        SkLearnModel(SGDRegressor))

        self._torch_model_wrappers = (TorchModel(self._torch_model),)
        self._torch_zero_model_wrappers = (TorchModel(self._zero_model),)
        
        self.modules = [
            ScaffoldServerModule(),
            GaussianNoiseModule(),
            YogiMomentumModule(),
            L2Clipping(),
            AdaGradModule(),
            YogiModule()]
        self.regularizers = [FedProxRegularizer(), LassoRegularizer(), RidgeRegularizer()]

    def tearDown(self) -> None:
        return super().tearDown()

    #@patch.multiple(BaseOptimizer, __abstractmethods__=set())  # disable base abstract to trigger errors if method(s) are not implemented
    def test_declearnoptimizer_01_init_invalid_model_arguments(self):

        correct_optimizers = (MagicMock(spec=FedOptimizer),
                              DecOptimizer(lrate=.1)
                                )
        incorrect_type_models = (None,
                                 True,
                                 np.array([1, 2, 3]),
                                 MagicMock(spec=BaseEstimator),
                                 nn.Module())

        for model in incorrect_type_models:
            for optimizer in correct_optimizers:
                with self.assertRaises(FedbiomedOptimizerError):
                    self._optim_wrapper(model, optimizer)

    def test_declearnoptimizer_02_init_invalid_optimizer_arguments(self):
        incorrect_optimizers = (None,
                                nn.Module(),
                                torch.optim.SGD(self._torch_model.parameters(), .01))
        correct_models = (
            MagicMock(spec=Model),
            MagicMock(spec=SkLearnModel)
        )

        for model in correct_models:
            for optim in incorrect_optimizers:

                with self.assertRaises(FedbiomedOptimizerError):
                    self._optim_wrapper(model, optim)


    def test_declearnoptimizer_03_step_method_1_TorchOptimizer(self):

        optim = FedOptimizer(lr=1)

        # initilise optimizer wrappers
        initialized_torch_optim_wrappers = (
            DeclearnOptimizer(copy.deepcopy(model), optim) for model in self._torch_zero_model_wrappers
            )

        fake_retrieved_grads = [
            {name: param for (name, param) in model.model.state_dict().items() }
            for model in self._torch_zero_model_wrappers
        ]

        fake_retrieved_grads = [TorchVector(grads) + 1 for grads in fake_retrieved_grads]
        # operation: do a SGD step with all gradients equal 1 and learning rate equals 1
        for torch_optim_wrapper, zero_model, grads in zip(initialized_torch_optim_wrappers,
                                                          self._torch_zero_model_wrappers,
                                                          fake_retrieved_grads):
            with patch.object(TorchModel, 'get_gradients') as get_gradients_patch:
                get_gradients_patch.return_value = grads.coefs

                torch_optim_wrapper.step()
                get_gradients_patch.assert_called_once()

            for (l, val), (l_ref, val_ref) in zip(zero_model.get_weights().items(), torch_optim_wrapper._model.get_weights().items()):

                self.assertTrue(torch.isclose(val - 1, val_ref).all())


    def test_declearnoptimizer_03_step_method_2_SklearnOptimizer(self):
        optim = FedOptimizer(lr=1)
        num_features = 4
        num_classes = 2

        # zero sklearn model weights
        for model in self._sklearn_model_wrappers:
            model.set_init_params({'n_features': num_features, 'n_classes': num_classes})
            model.model.eta0 = .1  # set learning rate to make sure it is different from 0
        initialized_sklearn_optim = (DeclearnOptimizer(copy.deepcopy(model), optim) for model in self._sklearn_model_wrappers)

        fake_retrieved_grads = [
            copy.deepcopy(model.get_weights()) for model in self._sklearn_model_wrappers
        ]
        fake_retrieved_grads = [NumpyVector(grads) + 1 for grads in fake_retrieved_grads]
        # operation: do a SGD step with all gradients equal 1 and learning rate equals 1
        for sklearn_optim_wrapper, zero_model, grads in zip(initialized_sklearn_optim,
                                                            self._sklearn_model_wrappers,
                                                            fake_retrieved_grads):
            with patch.object(BaseSkLearnModel, 'get_gradients') as get_gradients_patch:
                get_gradients_patch.return_value = grads.coefs
                sklearn_optim_wrapper.step()
                get_gradients_patch.assert_called()

            for (l, val), (l_ref, val_ref) in zip(zero_model.get_weights().items(),
                                                  sklearn_optim_wrapper._model.get_weights().items()):
                self.assertTrue(np.all(val - 1 == val_ref))  # NOTA: all val values are equal 0

    def test_declearnoptimizer_04_get_learning_rate(self):
        learning_rate = .12345
        optim = FedOptimizer(lr=learning_rate)

        # for torch specific optimizer wrapper

        for model in self._torch_model_wrappers:
            optim_wrapper = DeclearnOptimizer(model, optim)
            retrieved_lr = optim_wrapper.get_learning_rate()

            self.assertEqual(learning_rate, retrieved_lr[0])

        # for sckit-learn specific optimizer wrapper
        for model in self._sklearn_model_wrappers:
            optim_wrapper = DeclearnOptimizer(model, optim)
            retrieved_lr = optim_wrapper.get_learning_rate()

            self.assertEqual(learning_rate, retrieved_lr[0])

    def test_declearnoptimizer_05_aux_variables(self):
        learning_rate = .12345
        
        
        for model_wrappers in (self._torch_model_wrappers, self._sklearn_model_wrappers):
            for model in model_wrappers:
                optim = FedOptimizer(lr=learning_rate, modules = [ScaffoldServerModule()])
                optim_wrapper = DeclearnOptimizer(model, optim)
                empty_aux = {}
                optim_wrapper.set_aux(empty_aux)

                self.assertDictEqual(optim_wrapper.get_aux(), {})
                aux = { 'scaffold': 
                    {
                        'node-1': {'state': 1.},
                        'node-2': {'state': 2.},
                        'node-3': {'state': 3.}
                    }
                }

                optim_wrapper.set_aux(aux)
                
                collected_aux_vars = optim_wrapper.get_aux()
                expected_aux_vars = {
                    'scaffold':
                        {
                            'node-1': {'delta': -1.},
                            'node-2': {'delta': 0.},
                            'node-3': {'delta': 1.}
                        }
                }

                self.assertDictEqual(collected_aux_vars, expected_aux_vars)

                optim = FedOptimizer(lr=learning_rate, modules = [ScaffoldClientModule()])
                optim_wrapper = DeclearnOptimizer(model, optim)
                aux = {'scaffold': {'delta': 1.}}
                optim_wrapper.set_aux(aux)
                expected_aux = {'scaffold': {'state': 0.}}
                collected_aux = optim_wrapper.get_aux()
                self.assertDictEqual(expected_aux, collected_aux)
        
    def test_declearnoptimizer_06_states(self):
        def check_state(state: Dict[str, Any], learning_rate: float, w_decay: float, modules: List, regs: List, model):
            self.assertEqual(state['config']['lrate'], learning_rate)
            self.assertEqual(state['config']['w_decay'], w_decay)
            self.assertListEqual(state['config']['regularizers'], [(reg.name, reg.get_config()) for reg in regs])
            self.assertListEqual(state['config']['modules'], [(mod.name , mod.get_config()) for mod in modules])
            new_optim_wrapper = DeclearnOptimizer.load_state(model, state)
            self.assertDictEqual(new_optim_wrapper.save_state(), state)
            self.assertIsInstance(new_optim_wrapper.optimizer, FedOptimizer)

        learning_rate = .12345
        w_decay = .54321
        
        optim = FedOptimizer(lr=learning_rate, decay=w_decay)
        
        for model_wrappers in (self._torch_model_wrappers, self._sklearn_model_wrappers):
            for model in model_wrappers:
                optim_wrapper = DeclearnOptimizer(model, optim)
                state = optim_wrapper.save_state()
                
                check_state(state, learning_rate, w_decay, [], [], model)
        
        nb_tests = 10  # number of time the following test will be executed
        
        for model_wrappers in (self._torch_model_wrappers, self._sklearn_model_wrappers):
            for model in model_wrappers:
                for _ in range(nb_tests):
                    # test DeclearnOptimizer with random modules and regularizers 
                    selected_modules = random.sample(self.modules, random.randint(0, len(self.modules)))
                    selected_reg = random.sample(self.regularizers, random.randint(0, len(self.regularizers)))

                    optim = FedOptimizer(lr=learning_rate,
                                         decay=w_decay, 
                                         modules=selected_modules,
                                         regularizers=selected_reg)
                    optim_wrapper = DeclearnOptimizer(model, optim)
                    state = optim_wrapper.save_state()
                    
                    check_state(state, learning_rate, w_decay, selected_modules, selected_reg, model)

    def test_declearnoptimizer_05_declearn_optimizers(self):
        # TODO: test here several declearn optimizers, regardless of the framework used
        nb_tests = 10  # number of time the following test will be executed
        for _ in range(nb_tests):
            for model_wrappers in (self._torch_model_wrappers, self._sklearn_model_wrappers):
                for model in model_wrappers:
                
                    # test DeclearnOptimizer with random modules and regularizers 
                    selected_modules = random.sample(self.modules, random.randint(0, len(self.modules)))
                    selected_reg = random.sample(self.regularizers, random.randint(0, len(self.regularizers)))

                    optim = FedOptimizer(lr=.01,
                                         decay=.1, 
                                         modules=selected_modules,
                                         regularizers=selected_reg)
                    optim_wrapper = DeclearnOptimizer(model, optim)
    
    def test_declearnoptimizer_06_scaffold_1_sklearnModel(self):
        # test with one server and one node on a SklearnModel
        researcher_optim = FedOptimizer(lr=.01, modules=[ScaffoldServerModule()])
        
        node_optim = FedOptimizer(lr=.01, modules=[ScaffoldClientModule()])
        
        for model_wrappers in (self._torch_model_wrappers, self._sklearn_model_wrappers):
                for model in model_wrappers:
                    researcher_optim_wrapper = DeclearnOptimizer(model, researcher_optim)
                    node_optim_wrapper = DeclearnOptimizer(model, node_optim)


class TestTorchBasedOptimizer(unittest.TestCase):
    # make sure torch based optimizers does the same action on torch models - regardless of their nature
    def setUp(self):

        self._torch_model = (nn.Linear(4, 2),)
        self._fed_models = (TorchModel(model) for model in self._torch_model)
        self._zero_models = [copy.deepcopy(model) for model in self._torch_model]
        for model in self._zero_models:
            for p in model.parameters():
                p.data.fill_(0)

    def tearDown(self) -> None:
        return super().tearDown()

    def test_torchbasedoptimizer_01_zero_grad(self):
        declearn_optim = FedOptimizer(lr=.1)
        torch_optim_type = torch.optim.SGD

        for model in self._fed_models:
            # initialisation of declearn optimizer wrapper
            declearn_optim_wrapper = DeclearnOptimizer(model, declearn_optim)

            declearn_optim_wrapper.zero_grad()
            grads = declearn_optim_wrapper._model.get_gradients()

            for l, val in grads.items():
                self.assertTrue(torch.isclose(val, torch.zeros(val.shape)).all())

            # initialisation of native torch optimizer wrapper
            torch_optim = torch_optim_type(model.model.parameters(), .1)
            native_torch_optim_wrapper = NativeTorchOptimizer(model, torch_optim)
            native_torch_optim_wrapper.zero_grad()

            grads = native_torch_optim_wrapper._model.get_gradients()
            for l, val in grads.items():
                self.assertTrue(torch.isclose(val, torch.zeros(val.shape)).all())

    def test_torchbasedoptimizer_03_step(self):
        # check that declearn and torch optimizers give the same result
        declearn_optim = FedOptimizer(lr=1)
        torch_optim_type = torch.optim.SGD

        data = torch.Tensor([[1,1,1,1]])
        targets = torch.Tensor([[1, 1]])

        loss_func = torch.nn.MSELoss()

        for model in self._zero_models:
            model = TorchModel(model)
            # initialisation of declearn optimizer wrapper
            declearn_optim_wrapper = DeclearnOptimizer(model, declearn_optim)
            declearn_optim_wrapper.zero_grad()


            output = declearn_optim_wrapper._model.model.forward(data)

            loss = loss_func(output, targets)

            loss.backward()
            declearn_optim_wrapper.step()

            # initialisation of native torch optimizer wrapper
            torch_optim = torch_optim_type(model.model.parameters(), 1)
            native_torch_optim_wrapper = NativeTorchOptimizer(model, torch_optim)
            native_torch_optim_wrapper.zero_grad()

            output = native_torch_optim_wrapper._model.model.forward(data)

            loss = loss_func(output, targets)

            loss.backward()

            native_torch_optim_wrapper.step()

            # checks
            for (l, dec_optim_val), (l, torch_optim_val) in zip(declearn_optim_wrapper._model.get_weights().items(),
                                                                 native_torch_optim_wrapper._model.get_weights().items()):
                self.assertTrue(torch.isclose(dec_optim_val, torch_optim_val).all())

    def test_torchbasedoptimizer_04_invalid_methods(self):
        declearn_optim = FedOptimizer(lr=.1)

        for model in self._fed_models:
            # initialisation of declearn optimizer wrapper
            declearn_optim_wrapper = DeclearnOptimizer(model, declearn_optim)
            with self.assertRaises(FedbiomedOptimizerError):
                with declearn_optim_wrapper.optimizer_processing():
                    pass


class TestSklearnBasedOptimizer(unittest.TestCase):
    def setUp(self):
        pass
    
    def tearDown(self):
        pass

    def test_sklearnbasedoptimizer_01_get_learning_rate(self):
        pass
    
    def test_sklearnbasedoptimizer_02_step(self):
        pass
   
    def test_sklearnbasedoptimizer_03_optimizer_processing(self):
        pass

    def test_sklearnbasedoptimizer_04_invalid_method(self):
        # test that zero_grad raises error if model is pytorch
        torch_model = MagicMock(spec=TorchModel)
        pass
# class TestDeclearnTorchOptimizer(unittest.TestCase):

#     def test_declearntorchoptimizer_01_zero_grad_error(self):
#         declearn_optim = FedOptimizer(lr=.1)
#         model = MagicMock(spec=TorchModel)
#         model.model = Mock(spec=torch.nn.Module)
#         del model.model.zero_grad  # remove zero_grad method

#         dto = DeclearnTorchOptimizer(model, declearn_optim)
#         with self.assertRaises(FedbiomedOptimizerError):
#             dto.zero_grad()


class TestNativeTorchOptimizer(unittest.TestCase):
    pass
    # to be completed
class TestDeclearnSklearnOptimizer(unittest.TestCase):
    def test_declearnsklearnoptimizer_01_optimizer_post_processing(self):
        pass

if __name__ == "__main__":
    unittest.main()
