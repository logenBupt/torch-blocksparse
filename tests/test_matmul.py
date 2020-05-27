import torch
import torch_blocksparse
from time import time
from utils import *
from nose.tools import nottest
from parameterized import parameterized

# run reference implementation
@nottest
def run_mm_reference(x, w, mode, trans_a, trans_b, layout, block, dy):
  x = sparse_to_dense(x, layout, block) if mode == 'dsd' else x
  w = sparse_to_dense(w, layout, block) if mode == 'dds' else w
  x.retain_grad()
  w.retain_grad()
  xx = x.transpose(2, 3) if trans_a else x
  ww = w.transpose(2, 3) if trans_b else w
  y = torch.matmul(xx, ww)
  y = sparse_to_dense(y, layout, block) if mode == 'sdd' else y
  y.backward(dy)
  dx = x.grad.clone()
  dw = w.grad.clone()
  x.grad.zero_()
  w.grad.zero_()
  y = dense_to_sparse(y, layout, block) if mode == 'sdd' else y
  dx = dense_to_sparse(dx, layout, block) if mode == 'dsd' else dx
  dw = dense_to_sparse(dw, layout, block) if mode == 'dds' else dw
  return y, dx, dw

@nottest
def run_mm_triton(x, w, mode, trans_a, trans_b, layout, block, dy):
  x = dense_to_sparse(x, layout, block) if mode == 'dsd' else x
  w = dense_to_sparse(w, layout, block) if mode == 'dds' else w
  dy = dense_to_sparse(dy, layout, block) if mode == 'sdd' else dy
  op = torch_blocksparse.MatMul(layout, block, mode, trans_a=trans_a, trans_b=trans_b)
  x.retain_grad()
  w.retain_grad()
  y = op(x, w)
  y.backward(dy)
  dx = x.grad.clone()
  dw = w.grad.clone()
  x.grad.zero_()
  return y, dx, dw

@nottest
def init_inputs(Z, H, M, N, K, rho, mode, trans_a, trans_b, block, dtype):
  torch.manual_seed(1)
  AS0 = K if trans_a else M
  AS1 = M if trans_a else K
  BS0 = N if trans_b else K
  BS1 = K if trans_b else N
  shape = {'sdd': (M, N),
           'dsd': (AS0, AS1),
           'dds': (BS0, BS1)}[mode]
  layout = make_layout(rho, (H, shape[0]//block, shape[1]//block))
  x = torch.rand((Z, H, AS0, AS1), dtype=torch.float32, requires_grad=True).cuda()
  w = torch.rand((Z, H, BS0, BS1), dtype=torch.float32, requires_grad=True).cuda()
  dy = torch.rand((Z, H, M, N), dtype=torch.float32).cuda()
  x.retain_grad()
  w.retain_grad()
  return x, w, dy, layout

@nottest
def run_test_mm(Z, H, M, N, K, rho, mode, trans_a, trans_b, block, dtype):
  x, w, dy, layout = init_inputs(Z, H, M, N, K, rho, mode, trans_a, trans_b, block, dtype)
  ry, rdx, rdw = run_mm_reference(x.clone(), w.clone(), mode, trans_a, trans_b, layout, block, dy)
  ty, tdx, tdw = run_mm_triton(x.clone(), w.clone(), mode, trans_a, trans_b, layout, block, dy)
  ac_y = allclose(ry, ty)
  ac_dx = allclose(rdx, tdx)
  ac_dw = allclose(rdw, tdw)
  return ac_y, ac_dx, ac_dw

@nottest
def run_bench_mm(Z, H, M, N, K, rho, mode, trans_a, trans_b, block, dtype, repeat=10):
  x, w, dy, layout = init_inputs(Z, H, M, N, K, rho, mode, trans_a, trans_b, block, dtype)
  op = torch_blocksparse.MatMul(layout, block, mode, trans_a=trans_a, trans_b=trans_b)
  time = bench(lambda: op(x, w), repeat)
  flops = 2 * Z * layout.sum() * block * block
  return time

@parameterized(
    [
    (mode, at, bt, 32) for mode in ['sdd', 'dsd', 'dds']\
                       for at   in [False, True]\
                       for bt   in [False, True]\
    ]\
    +\
    [
    (mode, False, False, block) for mode in ['sdd', 'dsd', 'dds']\
                                for block in [16, 32, 64]\
    ]
)
def test_op(mode, at, bt, block):
  ac_y, ac_dx, ac_dw = run_test_mm(3, 2, 256, 512, 384, 0.5, mode, 
                                  at, bt, block, torch.float32)
  assert ac_y
  assert ac_dx
  assert ac_dw

def bench_op(mode, at, bt, block):
  run_bench_mm(3, 2, 256, 512, 384, 0.5, 'sdd', at, bt, block, torch.float32)