__all__ = [
    'template',
]

template = {

    "lettuce_native_{name}/__init__.py": """
import numpy as np
import torch


def _import_lettuce_native():
    import importlib
    return importlib.import_module("lettuce_native_{name}.cuda_native")


def _ensure_cuda_path():
    import os

    # on windows add cuda path for
    # cuda_native module to find all dll's
    if os.name == 'nt':
        os.add_dll_directory(os.path.join(os.environ['CUDA_PATH'], 'bin'))


# do not expose the os and importlib package
_ensure_cuda_path()
cuda_native = _import_lettuce_native()

# noinspection PyUnresolvedReferences,PyCallingNonCallable,PyStatementEffect
def invoke(simulation):
    {python_wrapper_before_buffer}

    if simulation.flow.stencil.d == 3:
        assert all(l % 8 == 0 for l in simulation.flow.f.shape[1:]), f"cuda_native requires all dimension of f to be a multiple of 8 (in 3d)"
    else:
        assert all(l % 16 == 0 for l in simulation.flow.f.shape[1:]), f"cuda_native requires all dimension of f to be a multiple of 16 (in 1d and 2d)"

    if simulation.no_collision_mask is not None:
        cuda_native.collide_and_stream_{name}(simulation.flow.f, simulation.flow.f_next, simulation.no_collision_mask, simulation.no_streaming_mask {cpp_wrapper_parameter_value})
    else:
        cuda_native.collide_and_stream_{name}(simulation.flow.f, simulation.flow.f_next {cpp_wrapper_parameter_value})
    {python_wrapper_after_buffer}
    simulation.flow.f, simulation.flow.f_next = simulation.flow.f_next, simulation.flow.f

""",

    "lettuce.cpp": """
#include "lettuce.hpp"

#define LETTUCE_DEBUG {debug}

#if LETTUCE_DEBUG
#define CHECK_CUDA(x) TORCH_CHECK((x).device().is_cuda(), #x " must be a CUDA tensor")
#define CHECK_CONTIGUOUS(x) TORCH_CHECK((x).is_contiguous(), #x " must be contiguous")
#endif

void
lettuce_{name}(at::Tensor f, at::Tensor f_next
#if {support_no_collision_mask}
  , at::Tensor no_collision_mask
#endif
#if {support_no_streaming_mask}
  , at::Tensor no_streaming_mask
#endif
 {cpp_wrapper_parameter})
{{
#if LETTUCE_DEBUG
    CHECK_CUDA(f); CHECK_CONTIGUOUS(f);
    CHECK_CUDA(f_next); CHECK_CONTIGUOUS(f_next);
#endif

    lettuce_cuda_{name}(f, f_next
#if {support_no_collision_mask}
      , no_collision_mask
#endif
#if {support_no_streaming_mask}
      , no_streaming_mask
#endif
      {cuda_wrapper_parameter_values});
}}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m)
{{
    m.def("collide_and_stream_{name}", &lettuce_{name}, "collide_and_stream_{name}");
}}
""",

    "lettuce.hpp": """
#ifndef LETTUCE_HPP
#define LETTUCE_HPP

#if _MSC_VER && !__INTEL_COMPILER
#pragma warning ( push )
#pragma warning ( disable : 4067 )
#pragma warning ( disable : 4624 )
#endif

#include <torch/extension.h>

#if _MSC_VER && !__INTEL_COMPILER
#pragma warning ( pop )
#endif

void
lettuce_cuda_{name}(at::Tensor f, at::Tensor f_next
#if {support_no_collision_mask}
  , at::Tensor no_collision_mask
#endif
#if {support_no_streaming_mask}
  , at::Tensor no_streaming_mask
#endif
  {cuda_wrapper_parameter});

void
lettuce_{name}(at::Tensor f, at::Tensor f_next
#if {support_no_collision_mask}
  , at::Tensor no_collision_mask
#endif
#if {support_no_streaming_mask}
  , at::Tensor no_streaming_mask
#endif
  {cpp_wrapper_parameter});

#endif
""",

    "lettuce_cuda.cu": """
#if _MSC_VER && !__INTEL_COMPILER
#pragma warning ( push )
#pragma warning ( disable : 4067 )
#pragma warning ( disable : 4624 )
#endif

#include <torch/torch.h>
#include <cuda.h>
#include <cuda_runtime.h>
#include <cstdint>

#if _MSC_VER && !__INTEL_COMPILER
#pragma warning ( pop )
#endif

using index_t = int;
using byte_t = unsigned char;

constexpr inline index_t clamp(index_t x, index_t max) {{
    [[unlikely]] if (x < 0)    return x + max;
    [[unlikely]] if (x >= max) return x - max;
    return x;
}}

#define d {d}
#define q {q}
#define cs {cs}

#if d == 1
#define thread_count dim3{{16u}}
#elif d == 2
#define thread_count dim3{{16u, 16u}}
#elif d == 3
#define thread_count dim3{{8u, 8u, 8u}}
#endif

#if d == 1
#define node() (index[0])
#elif d == 2
#define node() (index[0] * dimension[1] + index[1])
#elif d == 3
#define node() ((index[0] * dimension[1] + index[1])  * dimension[2] + index[2])
#endif

#if d == 1
#define distribution(q_) (q_ * dimension[0] + index[0])
#elif d == 2
#define distribution(q_) ((q_ * dimension[0] + index[0]) * dimension[1] + index[1])
#elif d == 3
#define distribution(q_) (((q_ * dimension[0] + index[0]) * dimension[1] + index[1])  * dimension[2] + index[2])
#endif

#if d == 1
#define neighbour(q_, sign_) (q_ * dimension[0] + clamp(index[0] sign_ e[q_][0], dimension[0]))
#elif d == 2
#define neighbour(q_, sign_) ((q_ * dimension[0] + clamp(index[0] sign_ e[q_][0], dimension[0])) * dimension[1] + clamp(index[1] sign_ e[q_][1], dimension[1]))
#elif d == 3
#define neighbour(q_, sign_) (((q_ * dimension[0] + clamp(index[0] sign_ e[q_][0], dimension[0])) * dimension[1] + clamp(index[1] sign_ e[q_][1], dimension[1]))  * dimension[2] + clamp(index[2] sign_ e[q_][2], dimension[2]))
#endif

#define read_(q_)         f_reg[q_] = f[dist_index[q_]];   ((void)0)
#define write_(q_)        f_next[dist_index[q_]] = f_reg[q_];   ((void)0)
#define stream_read_(q_)  f_reg[q_] = f[neighbour(q_, -)]; ((void)0)
#define stream_write_(q_) f_next[neighbour(q_, +)] = f_reg[q_]; ((void)0)

#if {enable_pre_streaming}
#if {support_no_streaming_mask}
#define read(q_) {{ if (no_streaming_mask[dist_index[q_]]) {{ read_(q_); }} else {{ stream_read_(q_); }} }} ((void)0)
#else
#define read(q_) {{ stream_read_(q_); }} ((void)0)
#endif
#else
#define read(q_) {{ read_(q_); }} ((void)0)
#endif

#if {enable_post_streaming}
#if {support_no_streaming_mask}
#define write(q_) {{ if (no_streaming_mask[dist_index[q_]]) {{ write_(q_); }} else {{ stream_write_(q_); }} }} ((void)0)
#else
#define write(q_) {{ stream_write_(q_); }} ((void)0)
#endif
#else
#define write(q_) {{ write_(q_); }} ((void)0)
#endif

template<typename scalar_t>
__global__ void
lettuce_cuda_{name}_kernel(scalar_t *f, scalar_t *f_next
#if {support_no_collision_mask}
  , std::uint8_t *no_collision_mask
#endif
#if {support_no_streaming_mask}
  , std::uint8_t *no_streaming_mask
#endif
  , index_t dimension0
#if d > 1
  , index_t dimension1
#endif
#if d > 2
  , index_t dimension2
#endif
  {kernel_parameter})
{{
  constexpr index_t e[q][d] = {e};
  constexpr scalar_t w[q] = {w};

  const index_t index[d] = {{
    static_cast<index_t>(blockIdx.x * blockDim.x + threadIdx.x)
#if d > 1
    , static_cast<index_t>(blockIdx.y * blockDim.y + threadIdx.y)
#endif
#if d > 2
    , static_cast<index_t>(blockIdx.z * blockDim.z + threadIdx.z)
#endif
  }};

  const index_t dimension[d] = {{
    dimension0,
#if d > 1
    dimension1,
#endif
#if d > 2
    dimension2,
#endif
  }};

#if {support_no_collision_mask}
  const index_t node_index = node();
#endif

  index_t dist_index[q];
#pragma unroll
  for (index_t i = 0; i < q; ++i)
    dist_index[i] = distribution(i);

  scalar_t f_reg[q];
#pragma unroll
  for (index_t i = 0; i < q; ++i)
    read(i);

  scalar_t rho = f_reg[0];
#pragma unroll
  for (index_t i = 1; i < q; ++i)
    rho += f_reg[i];

#if d > 1
  const scalar_t rho_inv = static_cast<scalar_t>(1.0) / rho;
#endif

  scalar_t u[d];
  {{
    scalar_t sum;
#pragma unroll
  for (index_t i = 0; i < d; ++i) {{
      sum = static_cast<scalar_t>(0.0);
#pragma unroll
      for (index_t j = 0; j < q; ++j)
        sum += e[j][i] * f_reg[j];
#if d == 1
      u[i] = sum / rho;
#else
      u[i] = sum * rho_inv;
#endif
    }}
  }}

  {global_buffer}
  {pipeline_buffer}

#pragma unroll
  for (index_t i = 0; i < q; ++i)
    write(i);
}}

void
lettuce_cuda_{name}(at::Tensor f, at::Tensor f_next
#if {support_no_collision_mask}
  , at::Tensor no_collision_mask
#endif
#if {support_no_streaming_mask}
  , at::Tensor no_streaming_mask
#endif
  {cuda_wrapper_parameter})
{{
  const index_t dimension[d] = {{
    static_cast<index_t> (f.sizes()[1]),
#if d > 1
    static_cast<index_t> (f.sizes()[2]),
#endif
#if d > 2
    static_cast<index_t> (f.sizes()[3]),
#endif
  }};

  assert((dimension[0] % thread_count.x) == 0u);
#if d > 1
  assert((dimension[1] % thread_count.y) == 0u);
#endif
#if d > 2
  assert((dimension[2] % thread_count.z) == 0u);
#endif

  const auto block_count = dim3{{
    dimension[0] / thread_count.x,
#if d > 1
    dimension[1] / thread_count.y,
#endif
#if d > 2
    dimension[2] / thread_count.z,
#endif
  }};

  {cuda_wrapper_buffer}

#if d == 1
#define DIMENSIONS_PARAMETER , dimension[0]
#elif d == 2
#define DIMENSIONS_PARAMETER , dimension[0], dimension[1]
#else
#define DIMENSIONS_PARAMETER , dimension[0], dimension[1], dimension[2]
#endif

#if {support_no_collision_mask}
#define NO_COLLISION_MASK_PARAMETER , no_collision_mask.data<std::uint8_t>()
#else
#define NO_COLLISION_MASK_PARAMETER
#endif

#if {support_no_streaming_mask}
#define NO_STREAMING_MASK_PARAMETER , no_streaming_mask.data<std::uint8_t>()
#else
#define NO_STREAMING_MASK_PARAMETER
#endif

  AT_DISPATCH_FLOATING_TYPES(f.scalar_type(), "lettuce_cuda_{name}", [&]{{
    lettuce_cuda_{name}_kernel<scalar_t><<<block_count, thread_count>>>(
      f.data<scalar_t>()
      , f_next.data<scalar_t>()
      NO_COLLISION_MASK_PARAMETER
      NO_STREAMING_MASK_PARAMETER
      DIMENSIONS_PARAMETER
      {kernel_parameter_values});
  }});
  torch::cuda::synchronize(-1);
}}
""",

    "setup.py": """
#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
from torch.utils.cpp_extension import BuildExtension, CUDAExtension
import os.path

install_requires = ['torch>=1.2']
setup_requires = []

native_sources = [
    os.path.join(os.path.dirname(__file__), 'lettuce.cpp'),
    os.path.join(os.path.dirname(__file__), 'lettuce_cuda.cu')]

setup(
    author='Robert Andreas Fritsch',
    author_email='info@robert-fritsch.de',
    maintainer='Andreas Kraemer',
    maintainer_email='kraemer.research@gmail.com',
    install_requires=install_requires,
    license='MIT license',
    keywords='lettuce',
    name='lettuce_native_{name}',
    ext_modules=[
        CUDAExtension(
            name='lettuce_native_{name}.cuda_native',
            sources=native_sources,
            extra_compile_args={{'cxx': [], 'nvcc': ['--ptxas-options', '--warn-on-spills,--allow-expensive-optimizations=true,-O3', '--use_fast_math', '--optimize', '3', '--maxrregcount', '128']}}
        )
    ],
    packages=find_packages(include=['lettuce_native_{name}']),
    setup_requires=setup_requires,
    url='https://github.com/lettucecfd/lettuce',
    version='{version}',
    cmdclass={{'build_ext': BuildExtension}},
    zip_safe=False,
)
""",

}
