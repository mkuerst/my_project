import re
import os
import torch
from my_project.utils.log_utils import init_logger
# from my_project.server.router.dynamic_prompt.shared_arr import SharedInt

logger = init_logger(__name__)


class MemoryManager:
    def __init__(self, size, dtype, head_num, head_dim, layer_num, always_copy=False):
        self.size = size
        self.dtype = dtype
        self.head_num = head_num
        self.head_dim = head_dim
        self.layer_num = layer_num
        self.always_copy = always_copy

        # mem_state 修改为使用计数方式，方便后期实现token共享机制，实现beam search 等
        self.mem_state = torch.zeros((size,), dtype=torch.int32, device="cuda")
        self.indexes = torch.arange(0, size, dtype=torch.long, device="cuda")
        self.can_use_mem_size = size

        # 用共享内存进行共享，router 模块读取进行精确的调度估计, nccl port 作为一个单机中单实列的标记。防止冲突。
        # from torch.distributed.distributed_c10d import _default_pg_init_method

        # nccl_port = re.search(r":(\d+)$", _default_pg_init_method).group(1)
        # assert nccl_port is not None
        # logger.info(f"mem manger get nccl port: {str(nccl_port)}")

        # self.shared_can_use_token_num = SharedInt(f"{str(nccl_port)}_mem_manger_can_use_token_num")

        # self.shared_can_use_token_num.set_value(self.can_use_mem_size)
        self._init_buffers(size, dtype, head_num, head_dim, layer_num)

    def _init_buffers(self, size, dtype, head_num, head_dim, layer_num):
        # Catch OOM when trying to allocate too much memory
        try:
            self.kv_buffer = [
                torch.empty((size, 2 * head_num, head_dim), dtype=dtype, device="cuda") for _ in range(layer_num)
            ]
        except Exception as e:
            logger.error(e, exc_info=True) 

    def _free_buffers(self):
        self.kv_buffer = None

    @torch.no_grad()
    def alloc(self, need_size):
        # if need_size > self.can_use_mem_size:
        #     logger.warn(f"warn no enough cache need_size {need_size} left_size {self.can_use_mem_size}")
        #     raise RuntimeError("OOM: Insufficient capaciy in KV Cache for scheduled reqs")
        #     return None
        can_use_index = torch.nonzero(self.mem_state == 0).view(-1)
        select_index = can_use_index[0:need_size]
        self.add_refs(select_index)
        return select_index

    @torch.no_grad()
    def alloc_contiguous(self, need_size):
        if self.always_copy:
            return None
        # if need_size > self.can_use_mem_size:
        #     logger.warn(f"warn no enough cache need_size {need_size} left_size {self.can_use_mem_size}")
        #     return None

        can_use_index = torch.nonzero(self.mem_state == 0).view(-1)
        can_use_index_size = len(can_use_index)
        can_use_index = can_use_index[0 : can_use_index_size - need_size + 1][
            (can_use_index[need_size - 1 :] - can_use_index[0 : can_use_index_size - need_size + 1]) == need_size - 1
        ]
        if can_use_index.shape[0] == 0:
            # logger.warn(f'Allocate contiguous: warn no enough cache need_size {need_size} left_size {self.can_use_mem_size}')
            return None
        start = can_use_index[0].item()
        end = start + need_size
        select_index = self.indexes[start:end]
        self.add_refs(select_index)
        return select_index, start, end

    @torch.no_grad()
    def free(self, free_index, num_tokens=0):
        """_summary_

        Args:
            free_index (torch.Tensor): _description_
        """
        free_index = free_index.long()
        self.decrease_refs(free_index, num_tokens)
        # logger.debug(f"FREED {len(free_index)} tokens: {free_index} ")
        # if self.can_use_mem_size == len(self.mem_state):
        #     logger.debug(f"freed all gpu mem size {self.can_use_mem_size}")
        return

    @torch.no_grad()
    def add_refs(self, token_index: torch.Tensor):
        # state = self.mem_state[token_index]
        # has_used_tokens = torch.count_nonzero(state).item()
        # all_tokens = len(state)
        # self.can_use_mem_size -= all_tokens - has_used_tokens
        # self.shared_can_use_token_num.set_value(self.can_use_mem_size)
        self.mem_state[token_index] += 1
        return

    @torch.no_grad()
    def decrease_refs(self, token_index: torch.Tensor, num_tokens=0):
        token_index, counts = token_index.unique(return_counts=True)
        self.mem_state[token_index] -= 1
        # state = self.mem_state[token_index]
        # used_tokens = torch.count_nonzero(state).item()
        # all_tokens = len(state)
        # self.can_use_mem_size += all_tokens - used_tokens
        # self.can_use_mem_size += (1024 + num_tokens)
        # self.shared_can_use_token_num.set_value(self.can_use_mem_size)
        return

    @torch.no_grad()
    def free_all(self):
        self.can_use_mem_size = len(self.mem_state)
        self.shared_can_use_token_num.set_value(self.can_use_mem_size)
        self.mem_state[:] = 0

    @torch.no_grad()
    def resize_mem(self, new_size):
        """
        just for test code
        """
        size = new_size
        dtype = self.dtype
        head_num = self.head_num
        head_dim = self.head_dim
        layer_num = self.layer_num

        self.mem_state = torch.zeros((size,), dtype=torch.int32, device="cuda")
        self.indexes = torch.arange(0, size, dtype=torch.long, device="cuda")
        self.can_use_mem_size = size
        # self.shared_can_use_token_num.set_value(self.can_use_mem_size)
        self._free_buffers()
        self._init_buffers(size, dtype, head_num, head_dim, layer_num)
        return
