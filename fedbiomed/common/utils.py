import sys
import inspect
from collections.abc import Iterable
from typing import Callable, Iterator, List, Union
from IPython.core.magics.code import extract_symbols

import torch
import numpy as np
from fedbiomed.common.exceptions import FedbiomedError


def get_class_source(cls) -> str:
    """
    Function for getting source of the class. It uses different methods for getting the class source based on
    shell type; IPython,Notebook shells or Python shell

    Args:
        cls: Class whose source code will be extracted

    Raises:
        FedbiomedError if arguemnt is not a class
    Return:
        str: Source code of the given class
    """

    if not inspect.isclass(cls):
        raise FedbiomedError('The argument `cls` must be a python class')

    # Check ipython status
    status = is_ipython()

    if status:
        file = _get_ipython_class_file(cls)
        codes = "".join(inspect.linecache.getlines(file))
        class_code = extract_symbols(codes, cls.__name__)[0][0]
        return class_code
    else:
        return inspect.getsource(cls)


def is_ipython() -> bool:
    """
    Function that checks whether the codes (function itself) is executed in ipython kernel or not

    Args:
        (None)

    Raises:
        (None)

    Returns:
        bool: If True python interpreter is IPython
    """

    ipython_shells = ['ZMQInteractiveShell', 'TerminalInteractiveShell']
    try:
        shell = get_ipython().__class__.__name__
        if shell in ipython_shells:
            return True
        else:
            return False
    except NameError:
        return False


def _get_ipython_class_file(cls) -> str:
    """
    Function that gets source file/cell-id of the class which is defined in ZMQInteractiveShell or
    TerminalInteractiveShell

    Args:
        cls (python class): Python class object defined on the IPython kernel

    Returns:
        str: Returns file path of Jupyter cell. On IPython's interactive shell, it returns cell ID
    """

    # Lookup by parent module
    if hasattr(cls, '__module__'):
        object_ = sys.modules.get(cls.__module__)
        # If module has `__file__` attribute
        if hasattr(object_, '__file__'):
            return object_.__file__

        # If parent module is __main__
        for name, member in inspect.getmembers(cls):
            if inspect.isfunction(member) and cls.__qualname__ + '.' + member.__name__ == member.__qualname__:
                return inspect.getfile(member)
    else:
        raise FedbiomedError(f'{cls} has no attribute `__module__`, source is not found.')


def get_method_spec(method: Callable):
    """
    Helper to get argument specification
    """

    method_spec = {}
    parameters = inspect.signature(method).parameters
    for (key, val) in parameters.items():
        method_spec[key] = {
            'name': val.name,
            'default': None if val.default is inspect._empty else val.default,
            'annotation': None if val.default is inspect._empty else val.default
        }

    return method_spec


def convert_to_python_float(value: Union[torch.tensor, np.integer, float, int]) -> float:
    """"""

    if not isinstance(value, (torch.Tensor, np.integer, float, int)):
        raise FedbiomedError(f"Converting {type(value)} to python to float is not supported.")

    # if the result is a tensor, convert it back to numpy
    if isinstance(value, torch.Tensor):
        value = value.numpy()

    if isinstance(value, Iterable) and value.size > 1:
        raise FedbiomedError("Can not convert array-type objects to float.")

    # if value is a numpy integer (not recognized as an int by python)
    if isinstance(value, np.integer):
        # convert numpy integer to a plain python integer
        value = int(value)

    return float(value)


def convert_iterator_to_list_of_python_floats(iterator: Iterator) -> List[float]:
    """"""

    if not isinstance(iterator, Iterable):
        raise FedbiomedError(f"object {type(iterator)} is not iterable")

    list_of_floats = []
    if isinstance(iterator, dict):
        # specific processing for dictionaries
        for val in iterator.values():
            list_of_floats.append(convert_to_python_float(val))
    else:
        for it in iterator:
            list_of_floats.append(convert_to_python_float(it))
    return list_of_floats
