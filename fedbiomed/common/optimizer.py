# coding: utf-8

"""Optimizer class wrapping the declearn-issued Optimizer."""

from typing import Any, Dict, Optional, Sequence, Tuple, Union

from declearn.model.api import Vector
from declearn.optimizer import Optimizer as DeclearnOptimizer
from declearn.optimizer.modules import OptiModule
from declearn.optimizer.regularizers import Regularizer
from typing_extensions import Self

from fedbiomed.common.constants import ErrorNumbers
from fedbiomed.common.exceptions import FedbiomedOptimizerError


class Optimizer:
    """Optimizer class, interfacing the declearn one to provide with a modular SGD-core algorithm."""

    def __init__(
        self,
        lr: float,
        decay: float = 0.0,
        modules: Optional[Sequence[Union[OptiModule, str, Tuple[str, Dict[str, Any]]]]] = None,
        regularizers: Optional[Sequence[Union[Regularizer, str, Tuple[str, Dict[str, Any]]]]] = None,
    ) -> None:
        """Instantiate the declearn-issued gradient-descent optimizer.

        Args:
            lr: Base learning rate (i.e. step size) applied to gradients-based
                updates upon applying them to a model's weights.
            decay: Optional weight decay parameter, used to parameterize a
                decoupled weight decay regularization term (see [1]) added to
                the updates right before the learning rate is applied and model
                weights are effectively updated.
            modules: Optional list of plug-in modules implementing gradients'
                alteration into model weights' udpates. Modules will be applied
                to gradients following this list's ordering.
                See `declearn.optimizer.modules.OptiModule` for details.
                See Notes section below for details on the "specs" format.
            regularizers: Optional list of plug-in loss regularizers.
                Regularizers will be applied to gradients following this list's
                order, prior to any other alteration (see `modules` above).
                See `declearn.optimizer.regularizers.Regularizer` for details.
                See Notes section below for details on the "specs" format.

        !!! info "Note"
            `Regularizer` and `OptiModule` to be used by this optimizer,
            specified using the `regularizers` and `modules` parameters,
            may be passed as ready-for-use instances, or be instantiated
            from specs, consisting either of a single string (the `name`
            attribute of the class to build) or a tuple grouping this
            name and a config dict (to specify some hyper-parameters).

        !!! info "References"
            [1] Loshchilov & Hutter, 2019.
                Decoupled Weight Decay Regularization.
                https://arxiv.org/abs/1711.05101
        """
        self._lr = lr
        self._decay = decay
        self._weights = None  # type: Optional[Vector]
        self._grads = None  # type: Optional[Vector]
        self._optimizer = DeclearnOptimizer(
            lrate=lr, w_decay=decay, modules=modules, regularizers=regularizers
        )

    def set_weights(self, weights: Vector) -> Vector:
        """Assign the weights that are to be used when `step` is called.

        Args:
            weights: Weights that are to be used when `step` is called,
                wrapped into a declearn Vector structure.

        Returns:
            weights: input weights.
        """
        self._weights = weights
        return weights

    def set_grads(self, grads: Vector) -> Vector:
        """Assign the gradients that are to be used when `step` is called.

        Args:
            grad: Gradients that are to be used when `step` is called,
                wrapped into a declearn Vector structure.

        Returns:
            gradients: input gradients.
        """
        self._grads = grads
        return grads

    def step(self) -> Vector:
        """Run an optimization step to compute and return model weight updates.

        Use the pre-assigned `weights` and `grads` (set using the `set_weights`
        and `set_grads` methods) to compute weight updates, using the pipeline
        defined by this instance.

        Returns:
            updates: Vector
                Updates to be applied to the model weights, computed by:
                - running wrapped gradients and weights through the regularizer
                  plug-ins (that add loss-regularization terms' derivatives);
                - running resulting gradients through the optimodule plug-ins
                  (that perform any defined gradient-alteration operation);
                - adding a decoupled weight-decay term, if one is to be used;
                - scaling the updates by the base learning rate.
                The results are wrapped into a declearn Vector structure, the
                concrete type of which is same as the weights and gradients.
        """
        grads = self._grads
        weights = self._weights
        assert grads is not None
        assert weights is not None
        # This code mostly replicates that of `declearn.optimizer.Optimizer.compute_updates_from_gradients`.
        # Add loss-regularization terms' derivatives to the raw gradients.
        for reg in self._optimizer.regularizers:
            grads = reg.run(grads, weights)
        # Iteratively refine updates by running them through the optimodules.
        for mod in self._optimizer.modules:
            grads = mod.run(grads)
        # Apply the base learning rate.
        updates = - self._lr * grads
        # Optionally add the decoupled weight decay term.
        if self._decay:
            updates -= self._decay * weights
        # Return the model updates.
        return updates

    def get_aux(self) -> Dict[str, Dict[str, Any]]:
        """Return auxiliary variables that need to be shared between the nodes and the researcher.

        Returns:
            aux: Dict that associates `module.collect_aux_var()` values to
                `module.name` keys for each and every module plugged in this
                Optimizer that has some auxiliary variables to share.
        """
        return self._optimizer.collect_aux_var()

    def set_aux(self, aux: Dict[str, Dict[str, Any]]) -> None:
        """Update plug-in modules based on received shared auxiliary variables.

        Args:
            aux: Auxiliary variables received from the counterpart optimizer
                (on the other side of the node-researcher frontier), that are
                to be a `{module.name: module.collect_aux_var()}` *or* a
                `{module.name: {node: module.collect_aux_var()}}` dict
                (depending on which side this optimizer is on).

        Raises:
            FedbiomedOptimizerError: If a key from `aux_var` does not match the
                name of any module plugged in this optimizer (i.e. if received
                variables cannot be mapped to a destinatory module).
        """
        try:
            self._optimizer.process_aux_var(aux)
        except KeyError as exc:
            raise FedbiomedOptimizerError(f"{ErrorNumbers.FB620}: `Optimizer.set_aux`: {exc}") from exc

    def save_state(self) -> Dict[str, Any]:
        """Return the configuration and current states of this Optimizer.

        This method is to be used for creating breakpoints.

        Returns:
            state: state-and-config dict that may be saved as part of a
                breakpoint file, and used to re-create this Optimizer
                using the `load_state` method.
        """
        config = self._optimizer.get_config()
        states = self._optimizer.get_state()
        return {"config": config, "states": states}

    @classmethod
    def load_state(cls, state: Dict[str, Any]) -> Self:
        """Instantiate an Optimizer from its breakpoint state dict.

        Args:
            state: state-and-config dict created using the `save_state` method.

        Returns:
            optimizer: Optimizer instance re-created from the `state` dict.
        """
        if not state.keys == {"config", "states"}:
            raise FedbiomedOptimizerError(f"{ErrorNumbers.FB620}: improper keys in `load_state` input dict.")
        optim = DeclearnOptimizer.from_config(state["config"])
        optim.set_state(state["states"])
        return cls(lr=optim.lrate, decay=optim.w_decay, modules=optim.modules, regularizers=optim.regularizers)
