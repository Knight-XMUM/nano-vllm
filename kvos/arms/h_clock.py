"""H 臂 —— CLOCK：每块 1 bit 访问位；指针扫到 bit==0 者驱逐，bit==1 清零继续。

元数据开销全场最小（H7 对照点）。H1/K0-1 的观测对象：请求粒度下整个
前缀链同 tick 置位 → 位饱和时 CLOCK 退化成 FIFO。probe 记录每次驱逐
扫描中 bit==1 的占比 —— 这就是饱和度的测量仪器。
"""

from kvos.hooks import Policy


class Clock(Policy):
    name = "H"

    def __init__(self):
        self.order = []   # context 块的环形队列（入架序）
        self.hand = 0
        self.probe = {"scanned": 0, "bits_set": 0}  # 饱和探针（K0-1 读数）

    def on_commit(self, blk, tick):
        if blk.hash not in self.order:
            self.order.append(blk.hash)

    def on_evict(self, candidates, tick):
        n = len(self.order)
        if n == 0:
            return min(candidates, key=lambda b: b.last_access).hash
        self.hand %= n
        for _ in range(2 * n):  # 最坏两轮：第一轮清位，第二轮必中
            if not self.order:
                break
            self.hand %= len(self.order)
            h = self.order[self.hand]
            blk = self.table.get(h)
            if blk is None or blk.plane != "context":
                self.order.pop(self.hand)  # 清掉残影（已驱逐/已重生的旧账）
                continue
            self.probe["scanned"] += 1
            if blk.ref_bit:
                self.probe["bits_set"] += 1
                blk.ref_bit = False
                self.hand += 1
            else:
                return self.order.pop(self.hand)
        # 理论保底（不该到达）：让 hand 处牺牲
        if not self.order:
            return min(candidates, key=lambda b: b.last_access).hash
        self.hand %= len(self.order)
        return self.order.pop(self.hand)

    def saturation(self):
        """驱逐扫描里 bit==1 的占比；越接近 1 说明访问位越形同虚设。"""
        s = self.probe["scanned"]
        return (self.probe["bits_set"] / s) if s else None
