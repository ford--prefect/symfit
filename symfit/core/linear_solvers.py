"""
Linear solvers solve linear problems.

Their API is a hybrid of our objectives and minimizers. This is because
they take in a model and some data, and output not only the tensor valued
parameters, but also a small fit report.
"""


import abc
from collections import OrderedDict
from six import add_metaclass

import numpy as np
from scipy.optimize import lsq_linear
from sympy import MatMul, diff

from symfit.core.models import variabletuple, ModelError
from symfit.core.support import key2str, sympy_to_py, cached_property
from symfit.core.fit_results import FitResults

def expr_is_linear(expr, p):
    """
    Check if the matrix expression ``expr`` is linear in ``p``.

    :param expr: symbolical expression.
    :param p: Matrix Parameter
    :return: bool
    """
    if isinstance(expr, MatMul):
        # Quick and dirty.
        if expr.args[0] == p:
            rest = MatMul(*expr.args[1:])
            return not rest.has(p)
        elif expr.args[-1] == p:
            rest = MatMul(*expr.args[:-1])
            return not rest.has(p)
        else:
            raise ModelError('Cannot deal with linear equations of this type. '
                             'Make sure {} is the first or last term in the '
                             'expression.'.format(p))
    elif expr is p:
        return True
    else:
        # More expensive. If the param is not in the derivative, we are linear.
        # Can not always be calculated, especially for matrix expr.
        dfdp = diff(expr, p).doit()
        return not dfdp.has(p)

@add_metaclass(abc.ABCMeta)
class BaseLinearSolver(object):
    """
    ABC for Linear solvers.
    """
    def __init__(self, model, data, scalar_parameters=None):
        """
        :param model: `symfit` style model.
        :param data: data for all the variables of the model.
        :param scalar_parameters: optional, a dict of values to use for the
            scalar parameters of ``model``, if any. Must be Parameter: value
            pairs. If not, it will be turned into that.
        """
        self.model = model
        self.data = data
        self.scalar_parameters = {} if scalar_parameters is None else scalar_parameters

    @property
    def scalar_parameters(self):
        return self._scalar_parameters

    @scalar_parameters.setter
    def scalar_parameters(self, input_dict):
        # scalar-parameters should always by indexed by Symbol, not name.
        for i, key in enumerate(sorted(input_dict, key=lambda p: p.name)):
            if isinstance(key, str):
                input_dict[self.model.scalar_params[i]] = input_dict.pop(key)
        self._scalar_parameters = input_dict

    @cached_property
    def subproblems(self):
        problems = {}
        for y in self.model.dependent_vars:
            for p in self.model.tensor_params:
                expr = self.model[y]
                if expr.has(p):
                    if expr_is_linear(expr, p):
                        expr = expr.xreplace({p: 1}).doit()
                        expr_func = sympy_to_py(expr, expr.free_symbols)
                        problems[p] = (expr_func, expr.free_symbols, y)
        if not problems:
            raise ModelError('No linear subproblem')
        return problems

    @property
    def subproblems_data(self):
        problems = {}
        for x, (A_func, A_symbols, y) in self.subproblems.items():
            relevant_data = {var: data for var, data in self.data.items()
                             if var in A_symbols}
            # Add values for scalar parameters as provided by scalar_parameters
            relevant_data.update(
                {param: value for param, value in self.scalar_parameters.items()
                 if param in A_symbols}
            )
            # Add fixed param values
            relevant_data.update(
                {param: param.value for param in self.model.scalar_params
                 if param not in self.model.free_params}
            )

            A_data = A_func(**key2str(relevant_data))
            problems[x] = (A_data, self.data[y])
        return problems

    @abc.abstractmethod
    def execute(self, *args, **kwargs):
        pass

class LstSq(BaseLinearSolver):
    def execute(self, *args, **kwargs):
        tensor_params = {}
        ans = {}
        for x, (A, y) in self.subproblems_data.items():
            res = np.linalg.lstsq(A, y, *args, **kwargs, rcond=None)
            tensor_params[x] = res[0]
            ans[x.name] = res
        res = FitResults(self.model, popt=[], covariance_matrix=None,
                         minimizer=None, objective=None, linear_solver=self,
                         message='', tensor_params=tensor_params, **ans)
        return res

class LstSqBounds(BaseLinearSolver):
    def execute(self, *args, **kwargs):
        tensor_params = {}
        ans = {}
        bounds = self.model.bounds
        for (x, (A, y)), (lb, ub) in zip(self.subproblems_data.items(), bounds):
            M = A.T.dot(A)
            DB = A.T.dot(y)
            x_res = []
            for lb_i, ub_i, DB_i in zip(lb.T, ub.T, DB.T):
                res = lsq_linear(M, DB_i, bounds=(lb_i, ub_i))
                x_res.append(res['x'][:, None])
            tensor_params[x] = np.block([[x_i for x_i in x_res]])
            ans[x.name] = res

        res = FitResults(self.model, popt=[], covariance_matrix=None,
                         minimizer=None, objective=None, linear_solver=self,
                         message='', tensor_params=tensor_params, **ans)
        return res