import inspect
from typing import Callable, Awaitable
type State = dict
# 状态就是字典，你后面往里面塞 "messages"、"intent"、"result" 随便什么 key
class AgentGraph:
    def __init__(self):
        self.nodes : dict[str,Callable] = {}  # 节点名 → 函数
        self.edges : list[tuple[str,str]] = []  # (from, to)
        self.conditions : dict[str,Callable] = {}  # from节点名 → 路由函数
        self.entry_point : str = ""    # 起始节点名
        self.interrupt_before : set[str] = set()  # 暂停点
    def add_node(self,name :str,func:Callable):
        """注册一个节点"""
        self.nodes[name] = func
    def add_edge(self,start :str,end:str):
        """普通边：start 节点跑完后自动去 end"""
        self.edges.append((start,end))
    def add_condition_edges(self,source:str,router:Callable,
                            mapping:dict[str,str]):
        """条件边：source 节点跑完后，根据 router 函数的返回值选下一个节点"""
        self.conditions[source] = lambda state:mapping[router(state)]

    async def invoke(self, state: State) -> State:
        """执行状态图：从 entry_point 开始，按边和条件一路跑到终点"""
        current = self.entry_point

        while current:
            # 跑当前节点（兼容 async 和普通函数）
            node_func = self.nodes[current]
            if inspect.iscoroutinefunction(node_func):
                state = await node_func(state)
            else:
                state = node_func(state)

            # 决定下一个节点去哪
            if current in self.conditions:
                route_fn = self.conditions[current]
                current = route_fn(state)
            else:
                old_current = current
                current = None
                for start, end in self.edges:
                    if start == old_current:
                        current = end
                        break

        return state

