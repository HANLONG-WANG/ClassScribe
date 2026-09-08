# Qwen 在 CUDA 可用机器上回退 CPU

在 RTX 4070、驱动 610.57.04、Qwen 独立环境 PyTorch 2.14.0+cu130 上复现：宿主环境 `torch.cuda.is_available()` 为真，但原 Bubblewrap 工作进程中为假，CUDA 初始化报 304（OS call failed）。Qwen 的 `device=auto` 因此选择 CPU。

原沙箱虽然映射 `/dev/nvidia*`，却没有映射驱动初始化还需要读取的 sysfs 模块信息。实验确认：只读映射 `/sys/module/nvidia` 和 `/sys/module/nvidia_uvm` 即可恢复 CUDA。只映射 PCI/设备树或动态链接缓存均不能修复。

完整 GPU 推理还依赖 Triton 调用 `/sbin/ldconfig -p` 查找驱动库。沙箱补上指向已挂载 `/usr/sbin` 的标准 `/sbin` 链接，并为 GPU 工作进程只读映射 `/etc/ld.so.cache`。Fedora 的 `/usr/bin/ld` 还通过 `/etc/alternatives/ld` 跳转，因此仅保留这个链接器映射，并要求它的真实目标在已挂载的 `/usr` 内。

修复仅在暴露 GPU 的工作进程中加入上述两个只读目录，保留网络和其他路径隔离，不映射整个 `/sys`。课堂 Qwen 调用发现 NVIDIA 计算设备时显式请求 `cuda:0`；初始化失败应返回加载错误，不再静默以 CPU 执行。成功加载后，活动快照可显示所请求并成功加载的 CUDA 设备。无 GPU 环境保留 `auto` 路径。

该修复不改动已锁定的 Qwen 模型或独立依赖环境，无需重装模型。已启动的后端进程需要重新启动才能使用新沙箱命令。

验证：修复后的生产调用器已用本机已安装的 Qwen 1.7B、显式 `cuda:0` 完成合成英文短音频识别并释放模型。新增 `tests/integration/test_cuda_worker_sandbox.py`，设置 `CLASSSCRIBE_CUDA_WORKER_PROJECT` 指向已有 worker 项目后，可验证沙箱内的真实 CUDA 张量运算及 Triton 编译，无需加载模型权重。
