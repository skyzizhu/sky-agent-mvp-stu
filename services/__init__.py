"""services/ —— 独立能力服务层。

与 common/（Agent 共享内核件）平级：这里放"对外部世界的能力"，
刻意不 import agent_core——Agent 内核不依赖本层，本层也不反向依赖内核。
"""
