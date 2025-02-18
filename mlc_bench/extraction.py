from typing import List, Tuple, Callable
import tvm
import tvm.relax
import dill

IMPORTS = """
import tvm
import dill
from mlc_bench.benchmark import MLCBench
from tvm.script import tir as T
"""

MAIN = """
if __name__ == "__main__":
    bench = MLCBench()
    for _ in range(SAMPLE_NUMBER):
        input_infos, median, std = bench.benchmark(
            main, input_shape_gen_func, "llvm -num-cores=4", tvm.cpu()
        )
        bench.record(FUNC_NAME, input_infos, median, std)
    bench.show()
"""


def get_shape_gen_func(func_args: List[Tuple[tvm.relax.expr.Call, str]]):
    def counted(func: Callable):
        def wrapped(*args, **kwargs):
            wrapped.count += 1
            return func(*args, **kwargs)

        wrapped.count = -1
        return wrapped

    def produce_shape(shape: tvm.relax.expr.ShapeExpr, count: int) -> Tuple[int]:
        produced = []
        for var in shape:
            if isinstance(var, tvm.tir.IntImm):
                produced.append(var.value)
            else:
                produced.append(2 ** (count % 13))
        return tuple(produced)

    @counted
    def input_shape_gen_func():
        results = []
        for arg in func_args:
            if isinstance(arg, tvm.relax.ShapeStructInfo):
                pass
            elif isinstance(arg, tvm.relax.struct_info.TensorStructInfo):
                results.append(
                    (
                        produce_shape(arg.shape, input_shape_gen_func.count),
                        arg.dtype,
                    )
                )
            else:
                for sub_arg in arg.fields:
                    results.append(
                        (
                            produce_shape(sub_arg.shape, input_shape_gen_func.count),
                            sub_arg.dtype,
                        )
                    )

        # work around wrong input order for integer input
        for arg in func_args:
            if isinstance(arg, tvm.relax.ShapeStructInfo):
                results.append(
                    (
                        (),
                        "int64",
                    )
                )
        return results

    return input_shape_gen_func


def extract_func(
    model_name: str,
    func_name: str,
    func: tvm.tir.PrimFunc,
    func_args: List[Tuple[List[Tuple[tvm.relax.expr.Call, str]], int]],
    file_path: str,
    category: int = -1,
    sample_number: int = 5,
):
    file = open(file_path, "w")
    print(IMPORTS, file=file)
    print(f'MODEL_NAME = "{model_name}"', file=file)
    print(f'FUNC_NAME = "{func_name}"', file=file)
    print(f"FUNC_HASH = {tvm.ir.structural_hash(func)}", file=file)
    print(f"WEIGHT = {func_args[0][1]}", file=file)
    print(f"CAT = {category}", file=file)
    print(f"SAMPLE_NUMBER = {sample_number}\n", file=file)
    print(
        f"input_shape_gen_func = dill.loads({dill.dumps(get_shape_gen_func(func_args[0][0]))})",
        file=file,
    )
    print(func.script(), file=file)
    print(MAIN, file=file)
    # func.show()


def extract_from_relax(mod: tvm.ir.IRModule, model_name: str, file_path: str):
    prim_funcs = {}

    for gv, func in mod.functions.items():
        if isinstance(func, tvm.tir.PrimFunc):
            prim_funcs[gv] = {"func": func, "args": []}

    for gv, func in mod.functions.items():
        if isinstance(func, tvm.relax.Function):
            for block in func.body.blocks:
                for binding in block.bindings:
                    if isinstance(binding.value, tvm.relax.expr.Call):
                        args = binding.value.args
                        gv = args[0]
                        if gv in prim_funcs:
                            assert isinstance(gv, tvm.ir.GlobalVar)
                            args = [arg.struct_info for arg in args[1:]] + [
                                binding.value.struct_info
                            ]
                            new_args = True
                            for i in range(len(prim_funcs[gv]["args"])):
                                arg, count = prim_funcs[gv]["args"][i]
                                if arg == args:
                                    prim_funcs[gv]["args"][i] = (arg, count + 1)
                                    new_args = False
                                    break
                            if new_args:
                                prim_funcs[gv]["args"].append((args, 1))

    for gv in prim_funcs:
        func_name = gv.astext().split("@")[1] if "@" in gv.astext() else gv.astext()
        extract_func(
            model_name=model_name,
            func_name=func_name,
            func=prim_funcs[gv]["func"],
            func_args=prim_funcs[gv]["args"],
            file_path=f"{file_path}/{func_name}.py",
        )


if __name__ == "__main__":
    raise NotImplementedError
