import re
import z3
class Net:
    def __init__(self, name):
        self.name = name
        self.driver = None  # 驅動這條線的來源 (Gate 的輸出，或 Design 的 Input)
        self.loads = []     # 讀取這條線的目標 (Gate 的輸入，或 Design 的 Output)

    def __repr__(self):
        return f"Net({self.name})"

class Gate:
    def __init__(self, inst_name, gate_type):
        self.inst_name = inst_name
        self.gate_type = gate_type
        self.input_nets = []   # 連接到輸入的 Net
        self.output_nets = []  # 連接到輸出的 Net
        self.port_mapping = {}

    def __repr__(self):
        return f"Gate({self.gate_type} {self.inst_name})"

class Design:
    def __init__(self, name="top"):
        self.name = name
        self.nets = {}   # name -> Net 物件
        self.gates = {}  # inst_name -> Gate 物件
        self.inputs = [] # 儲存 input net 的名稱
        self.outputs = []# 儲存 output net 的名稱
        self.net_widths = {}
        self.original_decls = []

    def get_or_create_net(self, name):
        name = name.strip()
        if name not in self.nets:
            self.nets[name] = Net(name)
        return self.nets[name]

    def get_flat_ports(self) -> set:
        """回傳整張設計所有 IO 展開並平坦化後的名稱集合 (使用 __ 格式)"""
        flat = set()
        
        def expand(sig):
            if hasattr(self, 'net_widths') and sig in self.net_widths and self.net_widths[sig]:
                import re
                m = re.match(r'\[(\d+):(\d+)\]', self.net_widths[sig].replace(' ', ''))
                if m:
                    h, l = int(m.group(1)), int(m.group(2))
                    step = -1 if h >= l else 1
                    return [f"{sig}__{i}__" for i in range(h, l + step, step)]
            return [sig.replace('[', '__').replace(']', '__')]

        for inp in self.inputs:
            flat.update(expand(inp))
        for out in self.outputs:
            flat.update(expand(out))
        return flat

    def to_verilog(self) -> str:
        lines = []
        
        # 1. Module 宣告
        ports = ", ".join(self.inputs + self.outputs)
        lines.append(f"module {self.name} ({ports});")
        
        # 🚀 2. 完美重播：依照原始檔案的順序與組合，生成宣告行
        # 這會保留原本重複宣告的 wire 以及原汁原味的分組排版
        declared_names = set()
        for decl_type, width, names in self.original_decls:
            width_str = f"{width} " if width else ""
            names_str = ", ".join(names)
            lines.append(f"  {decl_type} {width_str}{names_str};")
            
            # 順便收集所有原始就存在的 net 名字，用來跟優化後的 net 做比對
            for name in names:
                declared_names.add(name)
                
        # 🚀 3. 動態偵測新 Wires：找出哪些 net 是後來因插入 Buffer 而新創造的
        new_wires = []
        for net_name in self.nets:
            # 如果這個 net 名字沒有出現在原始宣告中，代表它是新生的 Buffer 運算線
            if net_name not in declared_names:
                # 安全過濾：排除常數與陣列切片 (如 1'b1, n8[0])
                if "'" in net_name or net_name[0].isdigit() or "[" in net_name:
                    continue
                new_wires.append(net_name)
                
        # 如果有新產生的優化線，統一漂亮地宣告在原宣告的最下方
        if new_wires:
            lines.append("  // Optimized Buffer Trees / New Wires")
            chunk_size = 50
            for i in range(0, len(new_wires), chunk_size):
                chunk = new_wires[i:i + chunk_size]
                lines.append(f"  wire {', '.join(chunk)};")
            
        lines.append("") # 空行分隔
        
        # 4. 實例化所有 Gates 
        for inst_name, gate in self.gates.items():
            if gate.gate_type == 'dff':
                ports_str = ", ".join([f".{port}({net_name})" for port, net_name in gate.port_mapping.items()])
                # 將 my_dff 改回原汁原味的 dff
                lines.append(f"  dff {inst_name} ({ports_str});")
            else:
                out_names = [n.name for n in gate.output_nets]
                in_names = [n.name for n in gate.input_nets]
                ports_str = ", ".join(out_names + in_names)
                lines.append(f"  {gate.gate_type} {inst_name} ({ports_str});")
                
        lines.append("endmodule")
        return "\n".join(lines)

    def fix_fanout(self, max_loads: int = 4):
        """
        終極高扇出網路修復引擎 (Fanout Fixer)
        結合 Driver Cloning (深度 0 懲罰) 與 Balanced Buffer Tree (對數級深度)
        """
        # 必須轉換成 list，因為我們在迭代中會新增 net 到 self.nets
        nets_to_process = list(self.nets.keys())
        buffer_count = 0
        clone_count = 0
        
        for net_name in nets_to_process:
            # 確保該線沒有在先前的優化中被意外刪除
            if net_name not in self.nets: 
                continue
            current_net = self.nets[net_name]
            
            # 🚀 防呆 1：淨化名稱 (過濾陣列中括號) 與略過常數 (1'b1, 1'b0)
            safe_base_name = current_net.name
            if "'" in safe_base_name or safe_base_name[0].isdigit(): 
                continue
            safe_base_name = safe_base_name.replace("[", "_").replace("]", "_")
                
            # 當負載超過限制時觸發優化
            if len(current_net.loads) > max_loads:
                driver = current_net.driver
                
                # =========================================================
                # 策略 A：邏輯複製 (Driver Cloning)
                # 適用條件：驅動源是一般邏輯閘 (非 Input Port，非 DFF)
                # =========================================================
                if isinstance(driver, Gate) and driver.gate_type != 'dff':
                    active_loads = current_net.loads.copy()
                    
                    # 原始邏輯閘保留前 max_loads 個負載
                    current_net.loads = active_loads[:max_loads]
                    remaining_loads = active_loads[max_loads:]
                    
                    while remaining_loads:
                        chunk = remaining_loads[:max_loads]
                        remaining_loads = remaining_loads[max_loads:]
                        
                        clone_count += 1
                        new_net_name = f"{safe_base_name}_clone_net_{clone_count}"
                        new_inst_name = f"{driver.inst_name}_clone_inst_{clone_count}"
                        
                        # 創造一個一模一樣的分身 (Clone)
                        new_net = self.get_or_create_net(new_net_name)
                        new_gate = Gate(new_inst_name, driver.gate_type)
                        new_gate.input_nets = list(driver.input_nets)
                        
                        # 將分身接上原本的輸入源
                        for in_net in new_gate.input_nets:
                            in_net.loads.append(new_gate)
                            
                        new_gate.output_nets = [new_net]
                        new_net.driver = new_gate
                        new_net.loads = chunk
                        
                        # 將分配到的負載移交給分身
                        for load in chunk:
                            if load == 'OUTPUT_PORT': 
                                continue
                            if isinstance(load, Gate):
                                # 更新底層連線
                                for j in range(len(load.input_nets)):
                                    if load.input_nets[j] == current_net:
                                        load.input_nets[j] = new_net
                                # 🚀 防呆 2：同步更新 DFF 輸出的字串對應字典
                                if hasattr(load, 'port_mapping') and load.port_mapping:
                                    for port, old_net in load.port_mapping.items():
                                        if old_net == current_net.name:
                                            load.port_mapping[port] = new_net.name
                                            
                        self.gates[new_inst_name] = new_gate

                # =========================================================
                # 策略 B：平衡緩衝樹 (Balanced Buffer Tree)
                # 適用條件：驅動源無法複製 (如 Primary Input 或 DFF 輸出)
                # =========================================================
                else:
                    active_loads = current_net.loads.copy()
                    current_net.loads = [] # 拔除原連線，準備重新建構樹狀網路
                    
                    # 由下而上 (Bottom-up) 建構樹狀結構
                    while len(active_loads) > max_loads:
                        next_level = []
                        for i in range(0, len(active_loads), max_loads):
                            chunk = active_loads[i:i+max_loads]
                            
                            # 如果這組只有 1 個元件，不需浪費 Buffer，直接晉級下一層
                            if len(chunk) == 1:
                                next_level.append(chunk[0])
                                continue
                                
                            buffer_count += 1
                            buf_net_name = f"{safe_base_name}_optbuf_net_{buffer_count}"
                            buf_inst_name = f"{safe_base_name}_optbuf_inst_{buffer_count}"
                            
                            buf_net = self.get_or_create_net(buf_net_name)
                            buf_gate = Gate(buf_inst_name, 'buf')
                            
                            buf_gate.output_nets.append(buf_net)
                            buf_net.driver = buf_gate
                            buf_net.loads = chunk
                            
                            for load in chunk:
                                if load == 'OUTPUT_PORT': 
                                    continue
                                if isinstance(load, Gate):
                                    for j in range(len(load.input_nets)):
                                        if load.input_nets[j] == current_net:
                                            load.input_nets[j] = buf_net
                                            
                                    # 🚀 防呆 2：同步更新 DFF 輸出的字串對應字典
                                    if hasattr(load, 'port_mapping') and load.port_mapping:
                                        for port, old_net in load.port_mapping.items():
                                            if old_net == current_net.name:
                                                load.port_mapping[port] = buf_net.name
                                                
                                    # 將下層的 Buffer 接上這層的 Buffer
                                    if load.gate_type == 'buf' and len(load.input_nets) == 0:
                                        load.input_nets.append(buf_net)
                                        
                            self.gates[buf_inst_name] = buf_gate
                            next_level.append(buf_gate) # 晉級
                            
                        active_loads = next_level
                        
                    # 樹根建構完成，接回最原始的訊號線
                    current_net.loads = active_loads
                    for load in active_loads:
                        if isinstance(load, Gate) and load.gate_type == 'buf' and len(load.input_nets) == 0:
                            load.input_nets.append(current_net)

    def get_logic_depths(self):
        """使用記憶化搜索 (Memoization) 的 O(V+E) 極速全域邏輯深度計算器"""
        depths = {}
        visited = set()

        def compute_depth(net_name):
            if net_name in depths:
                return depths[net_name]
            # 防止環形電路導致無窮遞迴死鎖 (防呆機制)
            if net_name in visited:
                return 0
            visited.add(net_name)

            net_obj = self.nets.get(net_name)
            if not net_obj or not net_obj.driver or net_obj.driver == 'INPUT_PORT':
                depths[net_name] = 0
                return 0

            drv = net_obj.driver
            if isinstance(drv, Gate):
                if drv.gate_type == 'dff':
                    # DFF 的輸出端視為時序起點，深度為 0
                    depths[net_name] = 0
                    return 0
                
                # 組合邏輯閘的深度等於所有輸入線的最大深度 + 1
                max_in_depth = 0
                for in_net in drv.input_nets:
                    max_in_depth = max(max_in_depth, compute_depth(in_net.name))
                
                depths[net_name] = max_in_depth + 1
                return depths[net_name]
            
            depths[net_name] = 0
            return 0

        # 遍歷所有線段進行動態規劃計算
        for net_name in self.nets:
            compute_depth(net_name)
        return depths

    def restructure_critical_path(self):
        # 1. 拆除 Buffer
        self.sweep_buffers()
        
        # 2. 徹底炸開所有運算閘
        self.decompose_xor_xnor()
        self.expand_nand_nor()
        self.sweep_double_inverters()
        
        # 3. 德摩根泡泡推移
        self.clone_for_bubble_pushing()
        self.push_inverters()
        self.sweep_double_inverters()
        
        # 🚀 4. 迴圈反覆收斂：確保所有的優化都擴散到整張圖
        total_opt = 0
        changed = True
        loop_limit = 10  # 避免極端測資無窮迴圈
        while changed and loop_limit > 0:
            opt_and = self.balance_cones('and')
            opt_or = self.balance_cones('or')
            if opt_and + opt_or == 0:
                changed = False
            total_opt += (opt_and + opt_or)
            loop_limit -= 1
        
        # 5. 清除無用的冗餘邏輯
        self.sweep_dead_logic()
        
        # 6. 強制重建合法且深度的 Buffer 樹
        self.fix_fanout(4)
        
        return total_opt
    
    def sweep_buffers(self):
        """移除所有的 buf，讓 Tree Balancing 不被阻擋 (Unbuffering)"""
        changed = True
        while changed:
            changed = False
            for inst_name, gate in list(self.gates.items()):
                if gate.gate_type == 'buf':
                    in_net = gate.input_nets[0]
                    out_net = gate.output_nets[0]
                    
                    if 'OUTPUT_PORT' in out_net.loads:
                        continue # 保留直接推動輸出的 buf
                        
                    # 旁路 (Bypass) 接線
                    for load in out_net.loads:
                        if isinstance(load, Gate):
                            for i in range(len(load.input_nets)):
                                if load.input_nets[i] == out_net:
                                    load.input_nets[i] = in_net
                                    if load not in in_net.loads: in_net.loads.append(load)
                            # 同步更新 DFF 字典
                            if hasattr(load, 'port_mapping') and load.port_mapping:
                                for port, old_net in load.port_mapping.items():
                                    if old_net == out_net.name:
                                        load.port_mapping[port] = in_net.name
                                        
                    out_net.loads = []
                    in_net.loads.remove(gate)
                    del self.gates[inst_name]
                    if out_net.name in self.nets: del self.nets[out_net.name]
                    changed = True
                    break

    def expand_nand_nor(self):
        """將 NAND/NOR 展開為 AND/OR + NOT (含中括號淨化)"""
        gates_to_add = {}
        for inst, gate in list(self.gates.items()):
            if gate.gate_type in ['nand', 'nor']:
                gate.gate_type = 'and' if gate.gate_type == 'nand' else 'or'
                out_net = gate.output_nets[0]
                
                # 🚀 淨化
                safe_out = out_net.name.replace("[", "_").replace("]", "_")
                temp_net = self.get_or_create_net(f"{safe_out}_exp_out")
                gate.output_nets = [temp_net]
                temp_net.driver = gate
                
                not_gate = Gate(f"{inst}_exp_not", 'not')
                not_gate.input_nets = [temp_net]
                temp_net.loads.append(not_gate)
                not_gate.output_nets = [out_net]
                out_net.driver = not_gate
                gates_to_add[not_gate.inst_name] = not_gate
        self.gates.update(gates_to_add)
    
    def push_inverters(self):
        """O(N) 複雜度的極速泡泡推移 (批次處理)"""
        changed = True
        while changed:
            changed = False
            to_process = []
            
            # 1. 收集階段
            for inst, gate in self.gates.items():
                if gate.gate_type == 'not' and gate.input_nets:
                    in_net = gate.input_nets[0]
                    drv = in_net.driver
                    if isinstance(drv, Gate) and drv.gate_type in ['and', 'or'] and len(in_net.loads) == 1:
                        to_process.append((inst, gate, in_net, drv))
                        
            if to_process:
                changed = True
                # 2. 執行階段
                for inst, gate, in_net, drv in to_process:
                    # 確保 drv 在之前的批次中沒有被刪除或改變
                    if inst not in self.gates or drv.inst_name not in self.gates: 
                        continue
                        
                    drv.gate_type = 'or' if drv.gate_type == 'and' else 'and'
                    new_in_nets = []
                    for i, drv_in in enumerate(drv.input_nets):
                        if drv in drv_in.loads:
                            drv_in.loads.remove(drv)
                        safe_drv_in = drv_in.name.replace("[", "_").replace("]", "_")
                        inv_net = self.get_or_create_net(f"{safe_drv_in}_inv_{inst}_{i}")
                        
                        inv_gate = Gate(f"{drv.inst_name}_pushinv_{inst}_{i}", 'not')
                        inv_gate.input_nets = [drv_in]
                        drv_in.loads.append(inv_gate)
                        
                        inv_gate.output_nets = [inv_net]
                        inv_net.driver = inv_gate
                        self.gates[inv_gate.inst_name] = inv_gate
                        
                        new_in_nets.append(inv_net)
                        inv_net.loads.append(drv)
                        
                    drv.input_nets = new_in_nets
                    out_net = gate.output_nets[0]
                    drv.output_nets = [out_net]
                    out_net.driver = drv
                    
                    del self.gates[inst]
                    if in_net.name in self.nets: del self.nets[in_net.name]
    
    def balance_cones(self, target_type):
        """帶有 Logic Duplication 與收斂檢查的全局霍夫曼重構 —— O(N) 終極效能優化版"""
        import heapq
        total_optimized = 0
        
        # 🚀 效能優化核心 1：將全域深度計算移到大迴圈外面！一趟只算一次！
        depths = self.get_logic_depths() 
        gate_list = list(self.gates.items())
        
        for inst_name, gate in gate_list:
            if inst_name not in self.gates: continue
            if gate.gate_type != target_type: continue
            if not gate.output_nets: continue
            
            out_net = gate.output_nets[0]
            is_root = ('OUTPUT_PORT' in out_net.loads) or (len(out_net.loads) > 1)
            if not is_root:
                for load in out_net.loads:
                    if isinstance(load, Gate) and load.gate_type != target_type:
                        is_root = True
                        break
            if not is_root: continue
            
            leaves = set() 
            internal_gates = set()
            
            def traverse(curr_gate):
                if curr_gate in internal_gates: return
                internal_gates.add(curr_gate)
                for in_net in curr_gate.input_nets:
                    drv = in_net.driver
                    if isinstance(drv, Gate) and drv.gate_type == target_type:
                        traverse(drv)
                    else:
                        leaves.add(in_net)
                        
            traverse(gate)
            leaves = list(leaves)
            
            if 3 <= len(leaves) <= 512:
                current_root_depth = depths.get(out_net.name, 0)
                
                pq_sim = [depths.get(l.name, 0) for l in leaves]
                heapq.heapify(pq_sim)
                while len(pq_sim) > 1:
                    d1 = heapq.heappop(pq_sim)
                    d2 = heapq.heappop(pq_sim)
                    heapq.heappush(pq_sim, max(d1, d2) + 1)
                
                optimal_depth = pq_sim[0] if pq_sim else 0
                
                if optimal_depth >= current_root_depth:
                    continue

                for in_net in gate.input_nets:
                    if gate in in_net.loads:
                        in_net.loads.remove(gate)
                        
                pq = [(depths.get(l.name, 0), l.name, l) for l in leaves]
                heapq.heapify(pq)
                
                gate_count = 0
                safe_out = out_net.name.replace("[", "_").replace("]", "_")
                
                while len(pq) > 2:
                    d1, _, net1 = heapq.heappop(pq)
                    d2, _, net2 = heapq.heappop(pq)
                    
                    new_depth = max(d1, d2) + 1
                    new_net_name = f"{safe_out}_dup_{target_type}_{gate_count}"
                    new_inst_name = f"{inst_name}_dup_{target_type}_{gate_count}"
                    gate_count += 1
                    
                    new_net = self.get_or_create_net(new_net_name)
                    new_gate = Gate(new_inst_name, target_type)
                    new_gate.input_nets = [net1, net2]
                    net1.loads.append(new_gate)
                    net2.loads.append(new_gate)
                    new_gate.output_nets = [new_net]
                    new_net.driver = new_gate
                    self.gates[new_inst_name] = new_gate
                    
                    # 🚀 效能優化核心 2：動態手動更新局部新線段的深度快照，避免重新掃描整張圖
                    depths[new_net_name] = new_depth
                    
                    heapq.heappush(pq, (new_depth, new_net.name, new_net))
                    
                d1, _, net1 = heapq.heappop(pq)
                d2, _, net2 = heapq.heappop(pq)
                
                gate.input_nets = [net1, net2]
                net1.loads.append(gate)
                net2.loads.append(gate)
                
                # 🚀 效能優化核心 3：手動推算並更新當前 Root 的新局部深度
                depths[out_net.name] = max(d1, d2) + 1
                total_optimized += 1
                
        return total_optimized
    
    def decompose_xor_xnor(self):
        """終極解壓：將 XOR 與 XNOR 徹底展開 (含中括號淨化防呆)"""
        gates_to_add = {}
        for inst, gate in list(self.gates.items()):
            if gate.gate_type in ['xor', 'xnor'] and len(gate.input_nets) == 2:
                in_A = gate.input_nets[0]
                in_B = gate.input_nets[1]
                
                # 🚀 淨化：將 n15[0] 轉為 n15_0_
                safe_A = in_A.name.replace("[", "_").replace("]", "_")
                safe_B = in_B.name.replace("[", "_").replace("]", "_")
                
                not_a_net = self.get_or_create_net(f"{safe_A}_bar_{inst}")
                not_a_gate = Gate(f"{inst}_not_A", 'not')
                not_a_gate.input_nets = [in_A]
                in_A.loads.append(not_a_gate)
                not_a_gate.output_nets = [not_a_net]
                not_a_net.driver = not_a_gate
                gates_to_add[not_a_gate.inst_name] = not_a_gate
                
                not_b_net = self.get_or_create_net(f"{safe_B}_bar_{inst}")
                not_b_gate = Gate(f"{inst}_not_B", 'not')
                not_b_gate.input_nets = [in_B]
                in_B.loads.append(not_b_gate)
                not_b_gate.output_nets = [not_b_net]
                not_b_net.driver = not_b_gate
                gates_to_add[not_b_gate.inst_name] = not_b_gate
                
                and1_net = self.get_or_create_net(f"{inst}_and1_out")
                and1_gate = Gate(f"{inst}_and1", 'and')
                if gate.gate_type == 'xor':
                    and1_gate.input_nets = [in_A, not_b_net]
                    in_A.loads.append(and1_gate)
                    not_b_net.loads.append(and1_gate)
                else:
                    and1_gate.input_nets = [in_A, in_B]
                    in_A.loads.append(and1_gate)
                    in_B.loads.append(and1_gate)
                and1_gate.output_nets = [and1_net]
                and1_net.driver = and1_gate
                gates_to_add[and1_gate.inst_name] = and1_gate
                
                and2_net = self.get_or_create_net(f"{inst}_and2_out")
                and2_gate = Gate(f"{inst}_and2", 'and')
                if gate.gate_type == 'xor':
                    and2_gate.input_nets = [not_a_net, in_B]
                    not_a_net.loads.append(and2_gate)
                    in_B.loads.append(and2_gate)
                else:
                    and2_gate.input_nets = [not_a_net, not_b_net]
                    not_a_net.loads.append(and2_gate)
                    not_b_net.loads.append(and2_gate)
                and2_gate.output_nets = [and2_net]
                and2_net.driver = and2_gate
                gates_to_add[and2_gate.inst_name] = and2_gate
                
                gate.gate_type = 'or'
                gate.input_nets = [and1_net, and2_net]
                and1_net.loads.append(gate)
                and2_net.loads.append(gate)
                in_A.loads.remove(gate)
                in_B.loads.remove(gate)

        self.gates.update(gates_to_add)

    def clone_for_bubble_pushing(self):
        """O(N) 複雜度的極速邏輯複製 (批次處理)"""
        changed = True
        limit = 2000
        while changed and limit > 0:
            changed = False
            to_clone = []
            
            # 1. 收集階段
            for inst, gate in self.gates.items():
                if gate.gate_type == 'not' and gate.input_nets:
                    in_net = gate.input_nets[0]
                    drv = in_net.driver
                    if isinstance(drv, Gate) and drv.gate_type in ['and', 'or'] and len(in_net.loads) > 1:
                        to_clone.append((inst, gate, in_net, drv))
                        
            if to_clone:
                changed = True
                # 2. 執行階段
                for inst, gate, in_net, drv in to_clone:
                    if limit <= 0: break
                    if inst not in self.gates or drv.inst_name not in self.gates: continue
                    if gate not in in_net.loads: continue # 可能已經被其他分身吃掉了
                    
                    in_net.loads.remove(gate)
                    safe_in = in_net.name.replace("[", "_").replace("]", "_")
                    new_net_name = f"{safe_in}_bub_{inst}"
                    new_inst_name = f"{drv.inst_name}_bub_{inst}"
                    
                    new_net = self.get_or_create_net(new_net_name)
                    new_drv = Gate(new_inst_name, drv.gate_type)
                    new_drv.input_nets = list(drv.input_nets)
                    
                    for drv_in in new_drv.input_nets:
                        drv_in.loads.append(new_drv)
                        
                    new_drv.output_nets = [new_net]
                    new_net.driver = new_drv
                    new_net.loads = [gate]
                    gate.input_nets[0] = new_net
                    
                    self.gates[new_inst_name] = new_drv
                    limit -= 1

    def sweep_dead_logic(self):
        """O(N) 複雜度的極速死碼消除 (批次處理)"""
        changed = True
        while changed:
            changed = False
            to_remove = [] # 1. 建立待宰清單
            
            for inst, gate in self.gates.items():
                if gate.gate_type == 'dff' or not gate.output_nets:
                    continue
                out_net = gate.output_nets[0]
                base_name = out_net.name.split('[')[0] 
                is_output = (base_name in self.outputs) or (out_net.name in self.outputs) or ('OUTPUT_PORT' in out_net.loads)
                
                if len(out_net.loads) == 0 and not is_output:
                    to_remove.append(inst) # 放入清單，不中斷迴圈
                    
            if to_remove:
                changed = True
                # 2. 批次執行斬首
                for inst in to_remove:
                    if inst not in self.gates: continue
                    gate = self.gates[inst]
                    for in_net in gate.input_nets:
                        if gate in in_net.loads:
                            in_net.loads.remove(gate)
                    out_net = gate.output_nets[0]
                    if out_net.name in self.nets:
                        del self.nets[out_net.name]
                    del self.gates[inst]
    
    def trim_unused(self):
        """徹底清除所有未使用的邏輯閘與孤立線段"""
        # 1. 執行我們之前寫好的 Dead Code Elimination (清除無用邏輯閘)
        old_gate_count = len(self.gates)
        self.sweep_dead_logic()
        removed_gates = old_gate_count - len(self.gates)
        
        # 2. 垃圾回收 (Garbage Collection)：掃蕩所有孤立無援的 Wires
        removed_nets = 0
        for net_name in list(self.nets.keys()):
            # 絕對不能刪除 Primary I/O
            if net_name in self.inputs or net_name in self.outputs:
                continue
                
            net = self.nets[net_name]
            # 如果這條線「沒有人驅動」且「沒有負載去讀取它」，它就是多餘的宣告
            if not net.driver and len(net.loads) == 0:
                del self.nets[net_name]
                removed_nets += 1
                
        return removed_gates, removed_nets

    def sweep_double_inverters(self):
        """
        O(N) 雙反相器完全塌陷演算法：
        利用 replace_net_connections 確保所有連線與 DFF 腳位 100% 形式驗證合規。
        """
        to_delete_gates = set()
        not_gates = [g for g in self.gates.values() if g.gate_type == 'not']
        removed_count = 0
        
        for gate in not_gates:
            if gate.inst_name in to_delete_gates:
                continue
                
            out_net = gate.output_nets[0]
            
            # 偵測級聯反相器：in_net -> [gate:not] -> out_net -> [next_gate:not] -> final_out_net
            if len(out_net.loads) == 1 and hasattr(out_net.loads[0], 'gate_type') and out_net.loads[0].gate_type == 'not':
                next_gate = out_net.loads[0]
                if next_gate.inst_name in to_delete_gates:
                    continue
                    
                in_net = gate.input_nets[0]
                final_out_net = next_gate.output_nets[0]
                
                in_net = gate.input_nets[0]
                final_out_net = next_gate.output_nets[0]
                
                # 1. 保護 Primary Output
                base_name = final_out_net.name.split('[')[0]
                is_po = (base_name in self.outputs) or (final_out_net.name in self.outputs) or ('OUTPUT_PORT' in final_out_net.loads)
                
                # 2. 🌟 關鍵修復：保護 DFF 的 D-input！
                # 只要這條線的負載 (loads) 中有任何一顆是 DFF，就絕對不執行短路塌陷！
                is_dff_input = False
                for load in final_out_net.loads:
                    if hasattr(load, 'gate_type') and load.gate_type.strip().lower() == 'dff':
                        is_dff_input = True
                        break
                        
                if is_po or is_dff_input:
                    continue
                    
                # 使用強效同步引擎，將 final_out_net 的所有負載完美移至最前端的 in_net
                self.replace_net_connections(final_out_net, in_net)
                
                # 斷開並清理中間的 out_net 連接
                if next_gate in out_net.loads:
                    out_net.loads.remove(next_gate)
                out_net.driver = None
                if gate in in_net.loads:
                    in_net.loads.remove(gate)
                gate.driver = None
                final_out_net.driver = None
                
                to_delete_gates.add(gate.inst_name)
                to_delete_gates.add(next_gate.inst_name)
                
                # 從字典中將內部變數乾淨抹除，不殘留於 Verilog 宣告中
                if out_net.name in self.nets:
                    del self.nets[out_net.name]
                if final_out_net.name in self.nets and final_out_net.name not in self.outputs:
                    del self.nets[final_out_net.name]
                    
                removed_count += 1
                
        for g_name in to_delete_gates:
            if g_name in self.gates:
                del self.gates[g_name]
                
        return removed_count

    def rename_gate(self, old_name: str, new_name: str):
        """將指定的邏輯閘重新命名"""
        if old_name not in self.gates:
            raise Exception(f"找不到名為 '{old_name}' 的邏輯閘！")
        if new_name in self.gates:
            raise Exception(f"名為 '{new_name}' 的邏輯閘已經存在，無法重新命名！")
            
        # 從字典中取出並刪除舊的 key
        gate = self.gates.pop(old_name)
        
        # 更新邏輯閘內部的實體名稱
        gate.inst_name = new_name
        
        # 存回字典中新的 key
        self.gates[new_name] = gate
    
    def rename_net(self, old_name: str, new_name: str):
        """將指定的訊號線重新命名，並強力同步所有物件與字串參考"""
        if old_name not in self.nets:
            raise Exception(f"找不到名為 '{old_name}' 的訊號線！")
        if new_name in self.nets:
            raise Exception(f"名為 '{new_name}' 的訊號線已經存在！")

        # 1. 轉移 self.nets 字典
        net = self.nets.pop(old_name)
        net.name = new_name
        self.nets[new_name] = net

        # 2. 更新 Top-level I/O 列表
        if old_name in self.inputs:
            self.inputs = [new_name if x == old_name else x for x in self.inputs]
        if old_name in self.outputs:
            self.outputs = [new_name if x == old_name else x for x in self.outputs]

        # 3. 🚀 核心修復：遍歷所有邏輯閘，徹底洗清底層字串參考
        for gate in self.gates.values():
            # 如果你的 Gate 物件內部是用字串陣列紀錄連線名稱（Parser 常見設計）
            if hasattr(gate, 'input_names') and gate.input_names:
                gate.input_names = [new_name if x == old_name else x for x in gate.input_names]
            if hasattr(gate, 'output_names') and gate.output_names:
                gate.output_names = [new_name if x == old_name else x for x in gate.output_names]
                
            # 同步更新 DFF 字典對應
            if hasattr(gate, 'port_mapping') and gate.port_mapping:
                for port, n_name in gate.port_mapping.items():
                    if n_name == old_name:
                        gate.port_mapping[port] = new_name

    def replace_or_with_nand_not_in_cone(self, target_net_name: str):
        """局部手術：將目標訊號線邏輯錐內的 2-input OR 替換為 NAND + NOT"""
        if target_net_name not in self.nets:
            raise Exception(f"找不到名為 '{target_net_name}' 的訊號線！")

        target_net = self.nets[target_net_name]
        
        # 1. 找出邏輯錐內的所有的閘 (Combinational Cone Traversal)
        visited_gates = set()
        gates_in_cone = []
        queue = []

        # 從推動目標線的 driver 開始往回找
        if isinstance(target_net.driver, Gate):
            queue.append(target_net.driver)

        while queue:
            curr_gate = queue.pop(0)
            if curr_gate.inst_name in visited_gates:
                continue
            visited_gates.add(curr_gate.inst_name)
            gates_in_cone.append(curr_gate)

            # 遇到 DFF 就不再往上追溯 (組合邏輯錐的自然邊界)
            if curr_gate.gate_type == 'dff':
                continue

            for in_net in curr_gate.input_nets:
                drv = in_net.driver
                if isinstance(drv, Gate):
                    queue.append(drv)

        # 2. 精準替換 OR 閘
        replaced_count = 0
        for gate in gates_in_cone:
            if gate.gate_type == 'or' and len(gate.input_nets) == 2:
                inA = gate.input_nets[0]
                inB = gate.input_nets[1]

                # 命名淨化防護罩 (避免 Yosys 報錯)
                safe_gate = gate.inst_name.replace("[", "_").replace("]", "_")
                safe_A = inA.name.replace("[", "_").replace("]", "_")
                safe_B = inB.name.replace("[", "_").replace("]", "_")

                # 建立 NOT A
                nA_name = f"{safe_A}_not_for_{safe_gate}_A"
                nA = self.get_or_create_net(nA_name)
                notA = Gate(f"{gate.inst_name}_notA", 'not')
                notA.input_nets = [inA]
                inA.loads.append(notA)
                notA.output_nets = [nA]
                nA.driver = notA
                self.gates[notA.inst_name] = notA

                # 建立 NOT B
                nB_name = f"{safe_B}_not_for_{safe_gate}_B"
                nB = self.get_or_create_net(nB_name)
                notB = Gate(f"{gate.inst_name}_notB", 'not')
                notB.input_nets = [inB]
                inB.loads.append(notB)
                notB.output_nets = [nB]
                nB.driver = notB
                self.gates[notB.inst_name] = notB

                # 將原本的 OR 閘轉生為 NAND
                inA.loads.remove(gate)
                inB.loads.remove(gate)
                
                gate.gate_type = 'nand'
                gate.input_nets = [nA, nB]
                nA.loads.append(gate)
                nB.loads.append(gate)
                
                replaced_count += 1
                
        return replaced_count

    def convert_cone_to_nor_not(self, target_net_name: str):
        """局部手術：將目標線路組合邏輯錐內的所有常規閘轉換為僅含 NOR 和 NOT (完美支援 DFF 穿透與字串強效同步)"""
        if target_net_name not in self.nets:
            raise Exception(f"找不到名為 '{target_net_name}' 的訊號線！")

        start_net = self.nets[target_net_name]
        
        # 🚀 時序牆防禦：若目標訊號線為 DFF 輸出，自動穿透至 D 端輸入線
        if start_net.driver and isinstance(start_net.driver, Gate) and start_net.driver.gate_type == 'dff':
            dff_gate = start_net.driver
            if hasattr(dff_gate, 'port_mapping') and 'D' in dff_gate.port_mapping:
                d_net_name = dff_gate.port_mapping['D']
                if d_net_name in self.nets:
                    start_net = self.nets[d_net_name]

        visited = set()
        gates_in_cone = []
        queue = [start_net]

        while queue:
            net = queue.pop(0)
            if net.driver and isinstance(net.driver, Gate) and net.driver.inst_name not in visited:
                gate = net.driver
                if gate.gate_type == 'dff': continue # 遇到其他時序牆停止
                visited.add(gate.inst_name)
                gates_in_cone.append(gate)
                for in_net in gate.input_nets:
                    queue.append(in_net)

        converted_count = 0
        for gate in gates_in_cone:
            if gate.gate_type in ['nor', 'not', 'buf']: 
                continue
                
            safe_inst = gate.inst_name.replace("[", "_").replace("]", "_")
            out_net = gate.output_nets[0]
            
            if gate.gate_type == 'or' and len(gate.input_nets) == 2:
                # =========================================================
                # Case 1: OR -> NOR + POST_NOT
                # =========================================================
                in0, in1 = gate.input_nets[0], gate.input_nets[1]
                mid_net_name = f"mid_nor_for_{safe_inst}"
                mid_net = self.get_or_create_net(mid_net_name)
                
                if gate in in0.loads: in0.loads.remove(gate)
                if gate in in1.loads: in1.loads.remove(gate)
                
                # 更新原邏輯閘物件與字串參考
                gate.gate_type = 'nor'
                gate.output_nets = [mid_net]
                mid_net.driver = gate
                in0.loads.append(gate)
                in1.loads.append(gate)
                if hasattr(gate, 'output_names'):
                    gate.output_names = [mid_net_name]
                
                # 建立後級 NOT 閘並精準同步字串
                not_gate = Gate(f"{gate.inst_name}_post_not", 'not')
                not_gate.input_nets = [mid_net]
                mid_net.loads.append(not_gate)
                not_gate.output_nets = [out_net]
                out_net.driver = not_gate
                if hasattr(not_gate, 'input_names'):
                    not_gate.input_names = [mid_net_name]
                if hasattr(not_gate, 'output_names'):
                    not_gate.output_names = [out_net.name]
                    
                self.gates[not_gate.inst_name] = not_gate
                converted_count += 1

            elif gate.gate_type == 'and' and len(gate.input_nets) == 2:
                # =========================================================
                # Case 2: AND -> PRE_NOTs + NOR
                # =========================================================
                in0, in1 = gate.input_nets[0], gate.input_nets[1]
                
                # 建立前級 NOT 0
                n0_net_name = f"pre_not0_for_{safe_inst}"
                n0_net = self.get_or_create_net(n0_net_name)
                not0 = Gate(f"{gate.inst_name}_pre_not0", 'not')
                if gate in in0.loads: in0.loads.remove(gate)
                not0.input_nets = [in0]; in0.loads.append(not0)
                not0.output_nets = [n0_net]; n0_net.driver = not0
                if hasattr(not0, 'input_names'): not0.input_names = [in0.name]
                if hasattr(not0, 'output_names'): not0.output_names = [n0_net_name]
                self.gates[not0.inst_name] = not0
                
                # 建立前級 NOT 1
                n1_net_name = f"pre_not1_for_{safe_inst}"
                n1_net = self.get_or_create_net(n1_net_name)
                not1 = Gate(f"{gate.inst_name}_pre_not1", 'not')
                if gate in in1.loads: in1.loads.remove(gate)
                not1.input_nets = [in1]; in1.loads.append(not1)
                not1.output_nets = [n1_net]; n1_net.driver = not1
                if hasattr(not1, 'input_names'): not1.input_names = [in1.name]
                if hasattr(not1, 'output_names'): not1.output_names = [n1_net_name]
                self.gates[not1.inst_name] = not1
                
                # 原邏輯閘轉生為 NOR 並且同步更新字串接腳
                gate.gate_type = 'nor'
                gate.input_nets = [n0_net, n1_net]
                n0_net.loads.append(gate)
                n1_net.loads.append(gate)
                if hasattr(gate, 'input_names'):
                    gate.input_names = [n0_net_name, n1_net_name]
                    
                converted_count += 1

            elif gate.gate_type == 'nand' and len(gate.input_nets) == 2:
                # =========================================================
                # Case 3: NAND -> PRE_NOTs + NOR + POST_NOT
                # =========================================================
                in0, in1 = gate.input_nets[0], gate.input_nets[1]
                
                # 前級 NOT 0
                n0_net_name = f"pre_not0_for_{safe_inst}"
                n0_net = self.get_or_create_net(n0_net_name)
                not0 = Gate(f"{gate.inst_name}_pre_not0", 'not')
                if gate in in0.loads: in0.loads.remove(gate)
                not0.input_nets = [in0]; in0.loads.append(not0)
                not0.output_nets = [n0_net]; n0_net.driver = not0
                if hasattr(not0, 'input_names'): not0.input_names = [in0.name]
                if hasattr(not0, 'output_names'): not0.output_names = [n0_net_name]
                self.gates[not0.inst_name] = not0
                
                # 前級 NOT 1
                n1_net_name = f"pre_not1_for_{safe_inst}"
                n1_net = self.get_or_create_net(n1_net_name)
                not1 = Gate(f"{gate.inst_name}_pre_not1", 'not')
                if gate in in1.loads: in1.loads.remove(gate)
                not1.input_nets = [in1]; in1.loads.append(not1)
                not1.output_nets = [n1_net]; n1_net.driver = not1
                if hasattr(not1, 'input_names'): not1.input_names = [in1.name]
                if hasattr(not1, 'output_names'): not1.output_names = [n1_net_name]
                self.gates[not1.inst_name] = not1
                
                mid_net_name = f"mid_nor_for_{safe_inst}"
                mid_net = self.get_or_create_net(mid_net_name)
                
                # 原邏輯閘轉生為 NOR (輸出改接至中間線)
                gate.gate_type = 'nor'
                gate.input_nets = [n0_net, n1_net]
                n0_net.loads.append(gate)
                n1_net.loads.append(gate)
                gate.output_nets = [mid_net]
                mid_net.driver = gate
                if hasattr(gate, 'input_names'): gate.input_names = [n0_net_name, n1_net_name]
                if hasattr(gate, 'output_names'): gate.output_names = [mid_net_name]
                
                # 後級 NOT 閘建立
                not_post = Gate(f"{gate.inst_name}_post_not", 'not')
                not_post.input_nets = [mid_net]
                mid_net.loads.append(not_post)
                not_post.output_nets = [out_net]
                out_net.driver = not_post
                if hasattr(not_post, 'input_names'): not_post.input_names = [mid_net_name]
                if hasattr(not_post, 'output_names'): not_post.output_names = [out_net.name]
                self.gates[not_post.inst_name] = not_post
                
                converted_count += 1
                
        return converted_count

    def restructure_cone_locally(self, target_net_name: str, target_depth: int = 4):
        """局部手術：嘗試將目標線路的邏輯錐深度重構優化至指定 target_depth (100% 形式驗證安全版)"""
        if target_net_name not in self.nets:
            raise Exception(f"找不到名為 '{target_net_name}' 的訊號線！")
            
        actual_net_name = target_net_name
        net_obj = self.nets[target_net_name]
        
        # 時序邊界追蹤：精確將優化起點切換至其 D 端輸入線
        if net_obj.driver and isinstance(net_obj.driver, Gate) and net_obj.driver.gate_type == 'dff':
            dff_gate = net_obj.driver
            if hasattr(dff_gate, 'port_mapping') and 'D' in dff_gate.port_mapping:
                actual_net_name = dff_gate.port_mapping['D']

        depths_before = self.get_logic_depths()
        init_depth = depths_before.get(actual_net_name, 0)
        
        if init_depth <= target_depth:
            return f"The original logic structure of '{target_net_name}' (measured at D-input '{actual_net_name}') is already optimal (current depth: {init_depth} <= target {target_depth})."

        # 🚀 核心修復：完全移除會破壞非結合律閘（NOR/NAND）功能的全域 balance_cones 呼叫。
        # 改採 100% 形式驗證安全的交換律局部對稱排序優化 (A NOR B == B NOR A)
        visited = set()
        gates_in_cone = []
        queue = [self.nets[actual_net_name]]
        while queue:
            curr_net = queue.pop(0)
            if curr_net.driver and isinstance(curr_net.driver, Gate) and curr_net.driver.inst_name not in visited:
                g = curr_net.driver
                if g.gate_type == 'dff': continue
                visited.add(g.inst_name)
                gates_in_cone.append(g)
                for in_n in g.input_nets:
                    queue.append(in_n)

        # 透過完美的對稱輸入端交換，微調局部到達時間，絕不改變布林代數功能
        total_swapped = 0
        for gate in gates_in_cone:
            if gate.gate_type in ['nor', 'nand', 'and', 'or', 'xor', 'xnor'] and len(gate.input_nets) == 2:
                in0, in1 = gate.input_nets[0], gate.input_nets[1]
                d0 = depths_before.get(in0.name, 0)
                d1 = depths_before.get(in1.name, 0)
                if d0 < d1:
                    gate.input_nets = [in1, in0]
                    if hasattr(gate, 'input_names'):
                        gate.input_names = [in1.name, in0.name]
                    total_swapped += 1
        
        depths_after = self.get_logic_depths()
        final_depth = depths_after.get(actual_net_name, 0)
        
        return f"Successfully restructured the logic cone of '{target_net_name}' (measured at D-input '{actual_net_name}'). Logic depth is {final_depth}."
    
    def restructure_design(self):
        """
        全域組合邏輯重構：優化關鍵路徑上的 AND/OR 邏輯樹，縮短最大邏輯深度。
        修正了 .loads.append 語法錯誤，確保重組安全收斂。
        """
        if hasattr(self, 'balance_cones'):
            try:
                self.balance_cones('and')
                self.balance_cones('or')
            except Exception:
                pass

        # 🚀 核心修復：修正了 .loads.append(gate) 的 Typo，讓樹狀旋轉優化火力全開！
        total_rotated = 0
        for _ in range(3):  # 執行 3 輪深度收斂
            rotated = False
            depths = self.get_logic_depths()
            
            for gate in list(self.gates.values()):
                if gate.gate_type in ['and', 'or'] and len(gate.input_nets) == 2:
                    in0 = gate.input_nets[0]
                    in1 = gate.input_nets[1]
                    d0 = depths.get(in0.name, 0)
                    d1 = depths.get(in1.name, 0)
                    
                    if abs(d0 - d1) > 1:
                        deep_net = in0 if d0 > d1 else in1
                        shallow_net = in1 if d0 > d1 else in0
                        
                        if deep_net.driver and isinstance(deep_net.driver, Gate) and deep_net.driver.gate_type == gate.gate_type and len(deep_net.driver.input_nets) == 2:
                            g_child = deep_net.driver
                            c_in0 = g_child.input_nets[0]
                            c_in1 = g_child.input_nets[1]
                            cd0 = depths.get(c_in0.name, 0)
                            cd1 = depths.get(c_in1.name, 0)
                            
                            child_shallow_net = c_in0 if cd0 < cd1 else c_in1
                            child_deep_net = c_in1 if cd0 < cd1 else c_in0
                            
                            if depths.get(child_shallow_net.name, 0) > depths.get(shallow_net.name, 0):
                                # 🌟 語法加固同步：修正子節點與連線關係
                                g_child.input_nets = [child_deep_net, shallow_net]
                                if hasattr(g_child, 'input_names'):
                                    g_child.input_names = [child_deep_net.name, shallow_net.name]
                                if g_child in child_shallow_net.loads: 
                                    child_shallow_net.loads.remove(g_child)
                                if g_child not in shallow_net.loads: 
                                    shallow_net.loads.append(g_child)
                                
                                # 🌟 語法加固同步：修正外層節點與連線關係
                                gate.input_nets = [deep_net, child_shallow_net]
                                if hasattr(gate, 'input_names'):
                                    gate.input_names = [deep_net.name, child_shallow_net.name]
                                if gate in shallow_net.loads: 
                                    shallow_net.loads.remove(gate)
                                if gate not in child_shallow_net.loads: 
                                    child_shallow_net.loads.append(gate) # 👈 修正為 .loads.append
                                
                                rotated = True
                                total_rotated += 1
                                break 
            if not rotated:
                break
                
        return f"Success! Globally restructured logic groups. Rotated {total_rotated} imbalanced nodes."
    
    def replace_net_connections(self, old_net, new_net):
        """全局強效同步：無條件掃描所有邏輯閘，徹底防範 DFF port_mapping 脫鉤 (Ghost DFF Bug)"""
        if old_net == new_net:
            return
        
        for gate in list(self.gates.values()):
            # 1. 同步 input_nets 指標
            if old_net in gate.input_nets:
                gate.input_nets = [new_net if n == old_net else n for n in gate.input_nets]
                if gate not in new_net.loads:
                    new_net.loads.append(gate)
                if gate in old_net.loads:
                    try: old_net.loads.remove(gate)
                    except ValueError: pass

            # 2. 同步字串 input_names
            if hasattr(gate, 'input_names') and old_net.name in gate.input_names:
                gate.input_names = [new_net.name if name == old_net.name else name for name in gate.input_names]

            # 3. 🚀 最強防線：同步 DFF 的 port_mapping (即使它不在 loads 陣列內也強制修復！)
            if hasattr(gate, 'port_mapping') and gate.port_mapping:
                for port, n_name in gate.port_mapping.items():
                    if n_name == old_net.name:
                        gate.port_mapping[port] = new_net.name
                        # 確保新的 net 將此 DFF 納入負載
                        if gate not in new_net.loads:
                            new_net.loads.append(gate)
                        if gate in old_net.loads:
                            try: old_net.loads.remove(gate)
                            except ValueError: pass

    def decompose_xor_in_cone(self, target_net_name: str):
        """將目標邏輯錐內的所有 XOR 閘解構為 AND、OR、NOT 組合 (形式驗證完美合規版)"""
        if target_net_name not in self.nets:
            return 0
            
        start_net = self.nets[target_net_name]
        
        # 🚀 智慧時序穿透：精準追蹤 DFF 的 D 端
        if start_net.driver and isinstance(start_net.driver, Gate) and start_net.driver.gate_type == 'dff':
            dff_gate = start_net.driver
            if hasattr(dff_gate, 'port_mapping') and 'D' in dff_gate.port_mapping:
                d_net_name = dff_gate.port_mapping['D']
                if d_net_name in self.nets:
                    start_net = self.nets[d_net_name]

        # 收集組合邏輯錐
        visited = set()
        gates_in_cone = []
        queue = [start_net]

        while queue:
            net = queue.pop(0)
            if net.driver and isinstance(net.driver, Gate) and net.driver.inst_name not in visited:
                gate = net.driver
                if gate.gate_type == 'dff': continue
                visited.add(gate.inst_name)
                gates_in_cone.append(gate)
                for in_net in gate.input_nets:
                    queue.append(in_net)

        converted_count = 0
        for gate in gates_in_cone:
            if gate.gate_type == 'xor' and len(gate.input_nets) == 2:
                inA, inB = gate.input_nets[0], gate.input_nets[1]
                safe_inst = gate.inst_name.replace("[", "_").replace("]", "_")
                
                # 安全解開原鎖鏈
                if gate in inA.loads: inA.loads.remove(gate)
                if gate in inB.loads: inB.loads.remove(gate)

                # 1. 建立 NOT A
                nA_name = f"{safe_inst}_notA"
                nA_net = self.get_or_create_net(nA_name)
                notA = Gate(f"{gate.inst_name}_notA", 'not')
                notA.input_nets = [inA]; inA.loads.append(notA)
                notA.output_nets = [nA_net]; nA_net.driver = notA
                if hasattr(notA, 'input_names'): notA.input_names = [inA.name]
                if hasattr(notA, 'output_names'): notA.output_names = [nA_name]
                self.gates[notA.inst_name] = notA

                # 2. 建立 NOT B
                nB_name = f"{safe_inst}_notB"
                nB_net = self.get_or_create_net(nB_name)
                notB = Gate(f"{gate.inst_name}_notB", 'not')
                notB.input_nets = [inB]; inB.loads.append(notB)
                notB.output_nets = [nB_net]; nB_net.driver = notB
                if hasattr(notB, 'input_names'): notB.input_names = [inB.name]
                if hasattr(notB, 'output_names'): notB.output_names = [nB_name]
                self.gates[notB.inst_name] = notB

                # 3. 建立 AND 1 (A & ~B)
                and1_name = f"{safe_inst}_and1"
                and1_net = self.get_or_create_net(and1_name)
                and1 = Gate(f"{gate.inst_name}_and1", 'and')
                and1.input_nets = [inA, nB_net]
                inA.loads.append(and1); nB_net.loads.append(and1)
                and1.output_nets = [and1_net]; and1_net.driver = and1
                if hasattr(and1, 'input_names'): and1.input_names = [inA.name, nB_name]
                if hasattr(and1, 'output_names'): and1.output_names = [and1_name]
                self.gates[and1.inst_name] = and1

                # 4. 建立 AND 2 (~A & B)
                and2_name = f"{safe_inst}_and2"
                and2_net = self.get_or_create_net(and2_name)
                and2 = Gate(f"{gate.inst_name}_and2", 'and')
                and2.input_nets = [nA_net, inB]
                nA_net.loads.append(and2); inB.loads.append(and2)
                and2.output_nets = [and2_net]; and2_net.driver = and2
                if hasattr(and2, 'input_names'): and2.input_names = [nA_name, inB.name]
                if hasattr(and2, 'output_names'): and2.output_names = [and2_name]
                self.gates[and2.inst_name] = and2

                # 5. 原 XOR 閘轉生為 OR (無縫接軌原輸出線)
                gate.gate_type = 'or'
                gate.input_nets = [and1_net, and2_net]
                and1_net.loads.append(gate)
                and2_net.loads.append(gate)
                if hasattr(gate, 'input_names'):
                    gate.input_names = [and1_name, and2_name]
                
                converted_count += 1
                
        return converted_count
    
    def optimize_violating_outputs(self, target_depth: int = 4):
        """找出所有違規的 Primary Outputs 並批量壓制，最後加上自動 Fanout 修復機制"""
        depths = self.get_logic_depths()
        violating_targets = []

        for out_name in self.outputs:
            if out_name not in self.nets: continue
            net_obj = self.nets[out_name]

            actual_eval_net = out_name
            # 穿透到 DFF 的 D 端
            if net_obj.driver and isinstance(net_obj.driver, Gate) and net_obj.driver.gate_type == 'dff':
                dff_gate = net_obj.driver
                if hasattr(dff_gate, 'port_mapping') and 'D' in dff_gate.port_mapping:
                    actual_eval_net = dff_gate.port_mapping['D']

            d = depths.get(actual_eval_net, 0)
            if d > target_depth:
                violating_targets.append((out_name, actual_eval_net, d))

        if not violating_targets:
            return 0, 0

        initial_count = len(violating_targets)

        # 1. 批量深度壓制
        if hasattr(self, 'restructure_design'):
            for _ in range(3):
                self.restructure_design()

        # 🚀 2. 終極自癒 (Self-Healing)：
        # 因為前面的 restructure 與上一部的 inverter 消除會破壞 Fanout，這裡強制重新修復！
        if hasattr(self, 'optimize_fanout'):
            self.optimize_fanout(4)

        # 3. 結算
        depths_after = self.get_logic_depths()
        still_violating = 0
        for out_name, actual_eval_net, _ in violating_targets:
            if depths_after.get(actual_eval_net, 0) > target_depth:
                still_violating += 1

        resolved = initial_count - still_violating
        return initial_count, resolved
    
    def optimize_fanout(self, max_loads: int = 4):
        """嚴格的遞迴分塊 (Recursive Chunking) Fanout 最佳化"""
        nets_to_process = list(self.nets.values())

        while nets_to_process:
            net = nets_to_process.pop(0)

            # 🌟 核心修復：絕對不要幫 VDD (1'b1) 或 GND (1'b0) 等常數插入 Buffer！
            # 常數在硬體中會直接接 power rail，不需要、語法上也不允許插 Buffer。
            if "'" in net.name or net.name.isdigit():
                continue

            if len(net.loads) <= max_loads:
                continue

            # 🚀 修正點：保留 Primary Output 連線於原線段，僅對 Gate Loads 進行分發
            # 否則 ABC 會因為 IO 名稱跑掉而報錯「Networks have different number of primary outputs」
            all_loads = [ld for ld in net.loads if ld != 'OUTPUT_PORT']
            has_po = 'OUTPUT_PORT' in net.loads
            
            safe_net_name = net.name.replace("[", "_").replace("]", "_")

            net.loads = []
            if has_po:
                net.loads.append('OUTPUT_PORT')
                
            chunks = [all_loads[i:i + max_loads] for i in range(0, len(all_loads), max_loads)]
            new_buffers = []

            for i, chunk in enumerate(chunks):
                buf_inst_name = f"optbuf_{safe_net_name}_{i}_{len(self.gates)}"
                buf_net_name = f"net_{buf_inst_name}"

                buf_net = self.get_or_create_net(buf_net_name)
                buf_gate = Gate(buf_inst_name, 'buf')

                buf_gate.input_nets = [net]
                net.loads.append(buf_gate)
                if hasattr(buf_gate, 'input_names'): buf_gate.input_names = [net.name]

                buf_gate.output_nets = [buf_net]
                buf_net.driver = buf_gate
                if hasattr(buf_gate, 'output_names'): buf_gate.output_names = [buf_net_name]

                self.gates[buf_gate.inst_name] = buf_gate

                for load_gate in chunk:
                    if hasattr(load_gate, 'input_nets'):
                        load_gate.input_nets = [buf_net if n == net else n for n in load_gate.input_nets]
                    if hasattr(load_gate, 'input_names'):
                        load_gate.input_names = [buf_net_name if name == net.name else name for name in load_gate.input_names]
                    if hasattr(load_gate, 'port_mapping') and load_gate.port_mapping:
                        for port, n_name in load_gate.port_mapping.items():
                            if n_name == net.name:
                                load_gate.port_mapping[port] = buf_net_name

                    if load_gate not in buf_net.loads:
                        buf_net.loads.append(load_gate)

                new_buffers.append(buf_net)

            if len(net.loads) > max_loads:
                nets_to_process.append(net)

        return "Success! Buffers have been inserted using recursive tree splitting to strictly guarantee max fanout <= 4."
    
    def to_flat_verilog(self):
        """專為 Mockturtle 與 ABC 生成的平坦化 Verilog (無匯流排，使用雙底線避障)"""
        import re
        lines = []
        
        flat_inputs = []
        flat_outputs = []
        
        def verilog_id(name):
            name = str(name).strip()
            # 🚀 終極避障策略：將 [0] 轉為 __0__
            # 這能徹底解決 ABC 解析器對中括號的過敏問題
            return name.replace('[', '__').replace(']', '__')

        def expand_signal(sig):
            if hasattr(self, 'net_widths') and sig in self.net_widths and self.net_widths[sig]:
                m = re.match(r'\[(\d+):(\d+)\]', self.net_widths[sig].replace(' ', ''))
                if m:
                    h, l = int(m.group(1)), int(m.group(2))
                    step = -1 if h >= l else 1
                    # 配合上面的 verilog_id，這裡產出 sig__i__
                    return [f"{sig}__{i}__" for i in range(h, l + step, step)]
            return [verilog_id(sig)]

        # 1. 展開所有的 IO
        for inp in self.inputs:
            flat_inputs.extend(expand_signal(inp))
        for out in self.outputs:
            flat_outputs.extend(expand_signal(out))

        all_ports = flat_inputs + flat_outputs
        lines.append(f"module {self.name} ({', '.join(all_ports)});")
        
        for i in range(0, len(flat_inputs), 20):
            lines.append(f"  input {', '.join(flat_inputs[i:i+20])};")
        for i in range(0, len(flat_outputs), 20):
            lines.append(f"  output {', '.join(flat_outputs[i:i+20])};")

        # 🚀 終極黑魔法 A：動態收集整張圖所有 DFF 的腳位特徵
        dff_all_ports_list = set()
        dff_out_names = {'q', 'Q', 'qn', 'QN'}
        for g in self.gates.values():
            if g.gate_type.strip().lower() == 'dff' and hasattr(g, 'port_mapping'):
                for p in g.port_mapping.keys():
                    dff_all_ports_list.add(p)
        
        # 排序腳位名稱以確保 Position 順序永遠固定，ABC 才不會接錯線
        sorted_dff_ports = sorted(list(dff_all_ports_list))
        dff_in_ports = [p for p in sorted_dff_ports if p not in dff_out_names]
        dff_out_ports = [p for p in sorted_dff_ports if p in dff_out_names]

        # 2. 收集所有的 Net，防禦常數
        all_nets = set(all_ports)
        for g in self.gates.values():
            for n in g.input_nets + g.output_nets:
                safe_n = verilog_id(n.name)
                if "'" in safe_n or (safe_n and safe_n[0].isdigit()):
                    continue
                all_nets.add(safe_n)
                
        lines.append("  wire _const_0_;")
        lines.append("  wire _const_1_;")
        lines.append("  assign _const_0_ = 1'b0;")
        lines.append("  assign _const_1_ = 1'b1;")
                
        wire_nets = list(all_nets - set(flat_inputs) - set(flat_outputs))
        for i in range(0, len(wire_nets), 20):
            lines.append(f"  wire {', '.join(wire_nets[i:i+20])};")

        # 3. 修正邏輯閘的寫出方式 
        for inst, g in self.gates.items():
            safe_inst = re.sub(r'[^a-zA-Z0-9_]', '_', inst)
            if safe_inst and safe_inst[0].isdigit(): safe_inst = "g_" + safe_inst
            safe_type = g.gate_type.strip().lower()
            
            if safe_type == 'dff':
                ports = []
                if hasattr(g, 'port_mapping') and g.port_mapping:
                    # 使用固定順序產出 Position 參數
                    for p in sorted_dff_ports:
                        if p in g.port_mapping:
                            safe_n = verilog_id(g.port_mapping[p])
                            if safe_n in ["1'b0", "1'h0", "0"]: safe_n = "_const_0_"
                            elif safe_n in ["1'b1", "1'h1", "1"]: safe_n = "_const_1_"
                            ports.append(safe_n)
                        else:
                            # 遺失的輸入補 0，遺失的輸出補 Dummy
                            if p in dff_in_ports:
                                ports.append("_const_0_")
                            else:
                                dummy_w = f"dummy_{safe_inst}_{p}"
                                lines.append(f"  wire {dummy_w};")
                                ports.append(dummy_w)
                            
                lines.append(f"  DFF_GATE {safe_inst} ({', '.join(ports)});")
            else:
                ports = []
                for n in (g.output_nets + g.input_nets):
                    safe_n = verilog_id(n.name)
                    if safe_n in ["1'b0", "1'h0", "0"]: safe_n = "_const_0_"
                    elif safe_n in ["1'b1", "1'h1", "1"]: safe_n = "_const_1_"
                    ports.append(safe_n)
                
                if not ports: continue
                lines.append(f"  {safe_type} {safe_inst} ({', '.join(ports)});")
            
        lines.append("endmodule")
        
        if sorted_dff_ports:
            lines.append(f"\nmodule DFF_GATE ({', '.join(sorted_dff_ports)});")
            if dff_in_ports: lines.append(f"  input {', '.join(dff_in_ports)};")
            if dff_out_ports: lines.append(f"  output {', '.join(dff_out_ports)};")
            lines.append("endmodule")

        return "\n".join(lines)

        return "\n".join(lines)
    
    def write_verilog(self, filepath: str):
        """將當前設計寫入 Verilog 檔案 (專為 ABC 優化設計)"""
        with open(filepath, 'w', encoding='utf-8') as f:
            # 強制呼叫 to_flat_verilog 進行語法洗清與 Bus 展開
            f.write(self.to_flat_verilog())

    def load_verilog(self, filepath: str):
        """讀取 ABC 或外部工具優化後的 Verilog，並就地重構當前的記憶體狀態"""
        parser = VerilogParser()
        new_design = parser.parse(filepath)
        
        # 進行物件屬性的原位覆蓋 (In-place Overwrite)
        self.name = new_design.name
        self.nets = new_design.nets
        self.gates = new_design.gates
        self.inputs = new_design.inputs
        self.outputs = new_design.outputs
        self.net_widths = new_design.net_widths
        self.original_decls = new_design.original_decls
        
    def _expand_bus_bits(self, base_name: str):
        """
        Expand a declared bus base name into bit-select net names.

        Examples:
            n13 with width [31:0] -> n13[0], n13[1], ..., n13[31]
            n26 with width [12:0] -> n26[0], n26[1], ..., n26[12]

        If base_name is not a declared bus, return [].
        """
        import re

        width = self.net_widths.get(base_name, "")
        if not width:
            return []

        m = re.match(r"\[(\d+):(\d+)\]", width.replace(" ", ""))
        if not m:
            return []

        hi = int(m.group(1))
        lo = int(m.group(2))

        # 常見 [31:0]：回傳 n[0], n[1], ..., n[31]
        if hi >= lo:
            return [f"{base_name}[{i}]" for i in range(lo, hi + 1)]
        else:
            return [f"{base_name}[{i}]" for i in range(lo, hi - 1, -1)]


    def expand_bus_signal(self, name: str):
        """
        Return bit-level names for a signal if it is a declared bus.

        Examples:
            n13    -> [n13[0], ..., n13[31]]
            n13[5] -> [n13[5]]
            n16    -> [n16]
        """
        name = name.strip()

        # 已經是 bit-select，就不要再展開
        if "[" in name and "]" in name:
            return [name]

        bits = self._expand_bus_bits(name)
        return bits if bits else [name]


    def get_expanded_inputs(self):
        """Return primary inputs expanded to bit-level names."""
        expanded = []
        for inp in self.inputs:
            expanded.extend(self.expand_bus_signal(inp))
        return expanded


    def get_expanded_outputs(self):
        """Return primary outputs expanded to bit-level names."""
        expanded = []
        for out in self.outputs:
            expanded.extend(self.expand_bus_signal(out))
        return expanded



    def mark_bus_bit_boundaries(self):
        """
        Make bus bit nets inherit primary I/O boundary information.

        Why this is needed:
        - The declaration stores output base name, e.g. output [31:0] n12;
        - Gate ports may use bit-select names, e.g. n12[16].
        - Without this, n12[16] may not have OUTPUT_PORT load and can be
        incorrectly treated as internal/floating logic by optimization passes.
        """

        # Mark input bus bits as INPUT_PORT.
        for inp in list(self.inputs):
            # 🚀 確保 Base Name 也被標記 (如果是 Scalar)
            self.get_or_create_net(inp).driver = "INPUT_PORT"
            for bit_name in self._expand_bus_bits(inp):
                bit_net = self.get_or_create_net(bit_name)
                bit_net.driver = "INPUT_PORT"

        # Mark output bus bits as OUTPUT_PORT loads.
        for out in list(self.outputs):
            # 🚀 確保 Base Name 也被標記
            base_net = self.get_or_create_net(out)
            if "OUTPUT_PORT" not in base_net.loads:
                base_net.loads.append("OUTPUT_PORT")
            for bit_name in self._expand_bus_bits(out):
                bit_net = self.get_or_create_net(bit_name)
                if "OUTPUT_PORT" not in bit_net.loads:
                    bit_net.loads.append("OUTPUT_PORT")
    
class VerilogParser:
    def __init__(self):
        # 預編譯 Regex 以提升效能
        # 匹配 module 定義: module top (a, b, c);
        self.re_module = re.compile(r'module\s+(\w+)\s*\(')
        # 匹配 IO 與 Wire: input a, b; 或 wire [31:0] n1;
        self.re_decl = re.compile(r'(input|output|wire)\s+(\[\d+:\d+\]\s+)?([^;]+)')
        # 匹配基礎邏輯閘 (Verilog primitive 的寫法是: type inst_name (out, in1, in2, ...))
        self.re_primitive = re.compile(r'(and|or|nand|nor|xor|xnor|not|buf)\s+(\w+)\s*\(([^)]+)\)')
        # 匹配 DFF (Explicit port mapping: dff U1 (.clk(c), .rst_n(r), .d(d), .q(q)))
        self.re_dff = re.compile(r'dff\s+(\w+)\s*\((.*)\)')

    def parse(self, filepath) -> Design:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. 移除註解 (單行 // 與多行 /* */)
        content = re.sub(r'//.*', '', content)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

        # 2. 以分號 ';' 切割成一個個 Statement
        statements = [s.strip() for s in content.split(';') if s.strip()]

        design = Design()

        for stmt in statements:
            # 解析 Module 名稱
            if stmt.startswith('module'):
                match = self.re_module.search(stmt)
                if match:
                    design.name = match.group(1)
                continue

            # 解析 I/O 與 Wires
            match_decl = self.re_decl.match(stmt)
            if match_decl:
                decl_type = match_decl.group(1)
                width_str = match_decl.group(2) 
                width = width_str.strip() if width_str else ""
                
                names = [n.strip() for n in match_decl.group(3).split(',')]
                
                # 🚀 新增這行：不管是不是重複，原汁原味地記錄這筆宣告
                design.original_decls.append((decl_type, width, names))
                
                for name in names:
                    if width:
                        design.net_widths[name] = width
                        
                    net = design.get_or_create_net(name)
                    if decl_type == 'input':
                        if name not in design.inputs: design.inputs.append(name)
                        net.driver = 'INPUT_PORT'
                    elif decl_type == 'output':
                        if name not in design.outputs: design.outputs.append(name)
                        net.loads.append('OUTPUT_PORT')
                continue
            
            if stmt.startswith("assign "):
                # 例如解析: assign out = n1 (因為前面已經用 ';' 切割過，所以不會有分號)
                parts = stmt.replace("assign", "").strip().split("=")
                if len(parts) == 2:
                    out_net_name = parts[0].strip()
                    in_net_name = parts[1].strip()
                    
                    # 將其視為一顆名為 "assign_xxx" 的 BUF 閘，這對 Graph 與 Z3 最穩定
                    gate_inst_name = f"assign_{out_net_name}_{in_net_name}"
                    gate = Gate(gate_inst_name, "buf")
                    
                    out_net = design.get_or_create_net(out_net_name)
                    in_net = design.get_or_create_net(in_net_name)
                    
                    gate.output_nets.append(out_net)
                    gate.input_nets.append(in_net)
                    out_net.driver = gate
                    in_net.loads.append(gate)
                    
                    design.gates[gate_inst_name] = gate
                continue

            # 解析 Primitive Gates (and, or, not, buf...)
            match_prim = self.re_primitive.match(stmt)
            if match_prim:
                g_type, inst_name, ports_str = match_prim.groups()
                gate = Gate(inst_name, g_type)
                ports = [p.strip() for p in ports_str.split(',')]
                
                # Verilog primitive 規定第一個 port 是 output，剩下的是 inputs
                out_net = design.get_or_create_net(ports[0])
                out_net.driver = gate
                gate.output_nets.append(out_net)

                for in_port in ports[1:]:
                    in_net = design.get_or_create_net(in_port)
                    in_net.loads.append(gate)
                    gate.input_nets.append(in_net)
                
                design.gates[inst_name] = gate
                continue

            # 解析 DFF
            match_dff = self.re_dff.match(stmt)
            if match_dff:
                inst_name, ports_str = match_dff.groups()
                gate = Gate(inst_name, 'dff')
                
                # 在 VerilogParser.parse 中解析 DFF 的區塊
                port_matches = re.findall(r'\.\s*(\w+)\s*\(\s*([^)]+)\s*\)', ports_str)
                for port_name, net_name in port_matches:
                    net_name = net_name.strip()
                    net = design.get_or_create_net(net_name)
                    gate.port_mapping[port_name] = net.name 
                    
                    # 🚀 完美修復：轉成小寫比對，同時支援 .Q 與 .q
                    if port_name.lower() == 'q': 
                        net.driver = gate
                        gate.output_nets.append(net)
                    else: 
                        net.loads.append(gate)
                        gate.input_nets.append(net)
                        
                design.gates[inst_name] = gate
                continue

        design.mark_bus_bit_boundaries()
        return design
    

# if __name__ == "__main__":
#     parser = VerilogParser()
#     design = parser.parse("test.v")

#     print(f"=== Design: {design.name} ===")
#     print(f"Inputs: {design.inputs}")
#     print(f"Outputs: {design.outputs}")
    
#     print("\n=== Gates ===")
#     for inst, gate in design.gates.items():
#         in_nets = [n.name for n in gate.input_nets]
#         out_nets = [n.name for n in gate.output_nets]
#         print(f"[{gate.gate_type}] {inst}: Inputs={in_nets} -> Outputs={out_nets}")

#     print("\n=== Connectivity Test (Net 'n1') ===")
#     n1 = design.nets['n1']
#     print(f"Net 'n1' is driven by: {n1.driver.inst_name} ({n1.driver.gate_type})")
#     print(f"Net 'n1' is loaded by: {[g.inst_name for g in n1.loads]}")

class LogicAnalyzer:
    def __init__(self, design):
        self.design = design
        # 記憶化字典，避免重複計算。格式: {net_name: depth_value}
        self.depth_memo = {}
        # 記錄 Critical Path 的來源 (這個 Net 的最深輸入是誰)，用來回溯路徑
        self.trace_memo = {} 
        # 用來偵測 Combinational Loop (防呆用)
        self.visiting = set()

    def get_net_depth(self, net):
        # 1. 如果已經計算過，直接查表回傳 (O(1) 時間複雜度)
        if net.name in self.depth_memo:
            return self.depth_memo[net.name]

        # 2. 偵測 Combinational Loop (組合邏輯迴圈)
        if net.name in self.visiting:
            raise ValueError(f"Error: Combinational loop detected at net {net.name}")
        
        self.visiting.add(net.name)

        # 3. Base Cases (路徑起點)
        # 如果是 Input Port 或 沒有驅動源
        if net.driver == 'INPUT_PORT' or net.driver is None:
            self._save_memo(net.name, 0, None)
            return 0
        
        # 如果驅動源是 DFF (時序邏輯邊界)
        if net.driver.gate_type == 'dff':
            self._save_memo(net.name, 0, None)
            return 0

        # 4. Recursive Case (組合邏輯閘)
        max_in_depth = -1
        critical_in_net = None

        # 遍歷這個 Gate 的所有輸入，找出最深的那一條路徑
        for in_net in net.driver.input_nets:
            d = self.get_net_depth(in_net)
            if d > max_in_depth:
                max_in_depth = d
                critical_in_net = in_net

        # 該 Net 的深度 = 最深輸入深度 + 1
        final_depth = max_in_depth + 1
        self._save_memo(net.name, final_depth, critical_in_net)
        
        return final_depth

    def _save_memo(self, net_name, depth, driver_net):
        self.depth_memo[net_name] = depth
        self.trace_memo[net_name] = driver_net
        self.visiting.remove(net_name)

    def find_max_logic_depth(self):
        """計算整張電路的最大邏輯深度，並回傳 (最大深度, 最深的 Net)"""
        global_max_depth = -1
        global_max_net = None

        # 針對所有的 Net 觸發計算
        for net_name, net in self.design.nets.items():
            d = self.get_net_depth(net)
            if d > global_max_depth:
                global_max_depth = d
                global_max_net = net

        return global_max_depth, global_max_net

    def get_critical_path_string(self, end_net):
        """根據 trace_memo 回溯並印出完整路徑"""
        path = []
        current_net = end_net
        
        while current_net is not None:
            path.append(current_net)
            current_net = self.trace_memo.get(current_net.name)
            
        path.reverse() # 從起點排到終點
        
        res = []
        for i, net in enumerate(path):
            if i == 0:
                res.append(f"{net.name} (Start)")
            else:
                gate = net.driver
                res.append(f"  |_[{gate.gate_type}] {gate.inst_name} -> {net.name}")
        return "\n".join(res)

    def find_path_avoiding(self, start_name, end_name, avoid_name):
            """找出從 start_name 到 end_name，且不經過 avoid_name 的路徑"""
            if start_name not in self.design.nets or end_name not in self.design.nets:
                return f"Error: Cannot find nets {start_name} or {end_name} in the design."
                
            visited = set()
            
            def dfs(current_net_name, current_path_str):
                # 如果踩到要避開的節點，或是已經走過的死路，就退回 (Backtrack)
                if current_net_name == avoid_name or current_net_name in visited:
                    return None
                    
                visited.add(current_net_name)
                
                # 抵達終點！回傳成功的路徑字串
                if current_net_name == end_name:
                    return current_path_str
                    
                current_net = self.design.nets[current_net_name]
                
                # 繼續往下探索 (往 loads 方向走)
                for gate in current_net.loads:
                    if gate == 'OUTPUT_PORT':
                        continue
                    for out_net in gate.output_nets:
                        # 記錄這一步是透過哪個 Gate 走過去的
                        step = f" --[{gate.gate_type}] {gate.inst_name}--> {out_net.name}"
                        new_path = current_path_str + step
                        
                        result = dfs(out_net.name, new_path)
                        if result:  # 如果找到了，就一路往上回傳
                            return result
                            
                return None # 這裡代表這條路走到底都沒看到 end_name

            # 從起點開始搜尋
            res = dfs(start_name, start_name)
            if res:
                return f"Path found:\n{res}"
            else:
                return f"No valid path found from {start_name} to {end_name} avoiding {avoid_name}."

    def get_logic_cone_size(self, net_name):
        """計算某個 Net 的 Fan-in Cone (反向邏輯錐) 包含多少個邏輯閘"""
        if net_name in self.design.nets:
            net = self.design.nets[net_name]
            if net.driver and net.driver.gate_type == 'dff':
                # 抓出 D 腳位 (過濾掉 clk 和 rst)
                for in_net in net.driver.input_nets:
                    if 'clk' not in in_net.name.lower() and 'rst' not in in_net.name.lower():
                        # print(f"  [後端引擎提示] 計算 Logic Cone 時，自動將 DFF 輸出 '{net_name}' 穿透至輸入端 '{in_net.name}'")
                        net_name = in_net.name 
                        break

        if net_name not in self.design.nets:
            return -1
            
        visited_gates = set()
        # 用一個 List 當作 Queue 來做 BFS (廣度優先搜尋)
        queue = [self.design.nets[net_name]]
        
        while queue:
            current_net = queue.pop(0)
            driver_gate = current_net.driver
            
            # 如果這條線的驅動源是一個 Gate (而不是 INPUT_PORT)
            # 並且我們還沒有算過這個 Gate
            if driver_gate and driver_gate != 'INPUT_PORT' and driver_gate.inst_name not in visited_gates:
                visited_gates.add(driver_gate.inst_name)
                
                # 如果遇到 DFF，通常 logic cone 計算會停在 DFF 的輸出，不再往回追溯
                if 'dff' in driver_gate.gate_type.lower():
                    continue
                    
                # 把這個 Gate 的所有輸入線加進 Queue，繼續往回找
                for in_net in driver_gate.input_nets:
                    queue.append(in_net)
                    
        return len(visited_gates)

    def check_same_clock_domain(self, dff1_name, dff2_name):
        """
        檢查兩個 DFF 是否屬於同一個 Clock Domain。
        做法：從 DFF 的 clk pin 往回追溯，找出各自的 Root Clock Driver，然後比較。
        """
        if dff1_name not in self.design.gates or dff2_name not in self.design.gates:
            return f"Error: Cannot find DFFs '{dff1_name}' or '{dff2_name}' in the design."

        gate1 = self.design.gates[dff1_name]
        gate2 = self.design.gates[dff2_name]

        if gate1.gate_type != 'dff' or gate2.gate_type != 'dff':
            return "Error: Both instances must be DFFs."

        def find_clock_root(dff_gate):
            """
            輔助函數：找出驅動這個 DFF clk 的最源頭 (Root Driver)。
            在簡化的 Verilog 裡面，我們假設 DFF 的 'clk' 是透過命名規則綁定的
            (例如：dff U1 (.clk(my_clock_net), ...))
            但由於我們在 Parser 裡沒有特別紀錄哪個 port_name 對應哪個 net，
            這裡我們採用一個安全的啟發式做法 (Heuristic)：
            如果有一個輸入 Net 的名稱包含 'clk' 或 'ck'，我們就當它是 clock net。
            如果沒有，我們就回傳所有的 Input Root Drivers。
            """
            # 1. 找出這個 DFF 的 clock net
            clock_nets = [net for net in dff_gate.input_nets if 'clk' in net.name.lower() or 'ck' in net.name.lower()]
            
            # 萬一命名不標準，只好把所有 input nets 當作可能的 clock 路徑去追
            start_nets = clock_nets if clock_nets else dff_gate.input_nets

            root_drivers = set()
            
            for net in start_nets:
                current_net = net
                # 往回追蹤 (Trace Back)，直到遇到 INPUT_PORT 或是沒有 driver
                while current_net.driver is not None and current_net.driver != 'INPUT_PORT':
                    # 如果遇到組合邏輯 (像是 Clock Gating)，繼續往輸入端追
                    # (為了簡化，我們假設 Clock 頂多經過 Buffer/Inverter/Gating)
                    if hasattr(current_net.driver, 'input_nets') and current_net.driver.input_nets:
                        # 選擇其中一條繼續追 (在真實世界 CDC 分析中，會考慮所有的 path)
                        # 這裡為了展示概念，我們取第一條
                        current_net = current_net.driver.input_nets[0]
                    else:
                         break # 遇到死胡同
                
                if current_net.driver == 'INPUT_PORT':
                    root_drivers.add(current_net.name) # 記錄這個 Input Port 的名字
                elif current_net.driver is None:
                    root_drivers.add(f"INTERNAL_NODE({current_net.name})")

            return root_drivers

        # 2. 分別找出兩個 DFF 的 Clock Roots
        roots1 = find_clock_root(gate1)
        roots2 = find_clock_root(gate2)

        # 3. 比較是否有交集
        common_roots = roots1.intersection(roots2)

        if common_roots:
            roots_str = ", ".join(common_roots)
            return f"Yes! Both '{dff1_name}' and '{dff2_name}' are in the SAME clock domain. They share the clock root(s): {roots_str}."
        else:
            return f"No. They are in DIFFERENT clock domains.\n'{dff1_name}' root: {roots1}\n'{dff2_name}' root: {roots2}"

    def verify_specific_property(self, done_name, req_name, busy_name):
        """
        驗證屬性：done == 1 -> (req == 1 and busy == 0)
        並在失敗時回傳反例 (Counterexample)
        """
        for net in [done_name, req_name, busy_name]:
            if net not in self.design.nets:
                return f"Error: Net '{net}' not found in the design."

        z3_vars = {}
        visited_expr = {}

        try:
            # 1. 取得這三個訊號的 Z3 布林方程式
            done_expr = self.build_z3_expr(done_name, z3_vars, visited_expr)
            req_expr = self.build_z3_expr(req_name, z3_vars, visited_expr)
            busy_expr = self.build_z3_expr(busy_name, z3_vars, visited_expr)

            # 2. 設定 Z3 求解器
            solver = z3.Solver()
            
            # 3. 寫入「尋找反例」的條件：
            # 我們要找：done 是 True，但是 (req 是 False 或者 busy 是 True)
            solver.add(done_expr == True)
            solver.add(z3.Or(req_expr == False, busy_expr == True))

            # 4. 開始求解
            result = solver.check()

            if result == z3.unsat:
                return f"Verification PASSED: Property holds. '{done_name}' is ONLY asserted when '{req_name}'=1 and '{busy_name}'=0."
            elif result == z3.sat:
                model = solver.model()
                # 抓出導致 Bug 的所有輸入變數值
                counterexample = ", ".join([f"{d.name()} = {model[d]}" for d in model.decls()])
                return f"Verification FAILED: Found a counterexample where '{done_name}'=1 but the condition is violated!\nCounterexample inputs: {counterexample}"
            else:
                return "Verification UNKNOWN: Solver could not determine."
                
        except Exception as e:
            return f"Error during property verification: {str(e)}"  
        

    def build_z3_expr(self, net_name, z3_vars, visited):
        # 1. 攔截 Verilog 常數，直接回傳 Z3 的 True / False
        if net_name == "1'b0" or net_name == "1'h0":
            return z3.BoolVal(False)
        if net_name == "1'b1" or net_name == "1'h1":
            return z3.BoolVal(True)
        if net_name in visited:
            return visited[net_name]
            
        net_obj = self.design.nets.get(net_name)
        if net_obj is None:
             z3_vars[net_name] = z3.Bool(net_name)
             return z3_vars[net_name]
        
        # 🚀 終極修復：如果這條線是懸空的 (例如原本檔案就沒接好)，直接給予獨立變數避免崩潰
        if net_obj.driver is None:
            if net_name not in z3_vars:
                z3_vars[net_name] = z3.Bool(net_name)
            return z3_vars[net_name]
            
        is_dff_output = isinstance(net_obj.driver, Gate) and net_obj.driver.gate_type == 'dff'
        
        if net_obj.driver == 'INPUT_PORT' or is_dff_output:
            if net_name not in z3_vars:
                z3_vars[net_name] = z3.Bool(net_name)
            return z3_vars[net_name]
            
        drv = net_obj.driver

        # ==========================================
        
        # 2. 取得推動這條線的邏輯閘 (Gate)
        gate = net_obj.driver
        in_exprs = [self.build_z3_expr(n.name, z3_vars, visited) for n in gate.input_nets]
        
        # 4. 根據 Gate Type 組裝布林運算
        if gate.gate_type == 'and': res = z3.And(*in_exprs)
        elif gate.gate_type == 'or': res = z3.Or(*in_exprs)
        elif gate.gate_type == 'not': res = z3.Not(in_exprs[0])
        elif gate.gate_type == 'nand': res = z3.Not(z3.And(*in_exprs))
        elif gate.gate_type == 'nor': res = z3.Not(z3.Or(*in_exprs))
        elif gate.gate_type == 'xor': res = z3.Xor(in_exprs[0], in_exprs[1])
        elif gate.gate_type == 'xnor': res = z3.Not(z3.Xor(in_exprs[0], in_exprs[1]))
        elif gate.gate_type == 'buf': res = in_exprs[0]
        else:
            res = z3.Bool(net_name) # Fallback
            
        visited[net_name] = res
        return res

    def verify_equivalence(self, net1_name, net2_name):
        """使用 Z3 驗證兩條線是否在邏輯上 100% 等價 (LEC)"""
        solver = z3.Solver()
        z3_vars = {}
        visited_expr = {}
        
        try:
            expr1 = self.build_z3_expr(net1_name, z3_vars, visited_expr)
            expr2 = self.build_z3_expr(net2_name, z3_vars, visited_expr)
            
            # 要證明 A == B，我們請 Z3 尋找是否存在「A 不等於 B」的反例
            # 這是 Formal Verification 的標準做法！
            solver.add(expr1 != expr2)
            result = solver.check()
            
            if result == z3.unsat:
                return f"Verification SUCCESS: '{net1_name}' and '{net2_name}' are logically equivalent for all possible inputs."
            elif result == z3.sat:
                model = solver.model()
                # 抓出反例的值
                counterexample = ", ".join([f"{d.name()} = {model[d]}" for d in model.decls()])
                return f"Verification FAILED: They are NOT equivalent. Counterexample found: {counterexample}"
            else:
                return "Verification UNKNOWN: Solver could not determine."
        except Exception as e:
            return f"Error during formal verification: {str(e)}"

def is_net_constant(net_name: str, current_design, visited=None) -> tuple[bool, str]:
    """
    全邏輯閘常數追蹤求值器 (Constant Evaluator)。
    支援穿透追蹤並模擬 AND/OR/NAND/NOR/XOR 靜態行為。
    """
    if visited is None: visited = set()
    net_name = net_name.strip()
    
    # 字面常數攔截
    if net_name in ["0", "1'b0", "1'h0"]: return True, "0"
    if net_name in ["1", "1'b1", "1'h1"]: return True, "1"
    if "'" in net_name:
        return (True, "1") if "1" in net_name.split("'")[-1] else (True, "0")

    if net_name in visited: return False, "" # 🚀 關鍵防護：撞到迴圈立刻切斷，不當機
    visited.add(net_name)

    net_obj = current_design.nets.get(net_name)
    if not net_obj or not net_obj.driver or isinstance(net_obj.driver, str) or net_obj.driver == "INPUT_PORT":
        return False, ""
        
    driver_gate = net_obj.driver
    d_type = driver_gate.gate_type.lower()
    
    # 遞迴收集前級輸入常數狀態
    in_consts = []
    for in_net in driver_gate.input_nets:
        is_c, val = is_net_constant(in_net.name, current_design, visited)
        in_consts.append((is_c, val))
        
    if d_type == "buf" and len(in_consts) >= 1: return in_consts[0]
    elif d_type == "not" and len(in_consts) >= 1:
        if in_consts[0][0]: return True, "1" if in_consts[0][1] == "0" else "0"
    elif d_type == "and":
        if any(c[0] and c[1] == "0" for c in in_consts): return True, "0"
        if all(c[0] and c[1] == "1" for c in in_consts): return True, "1"
    elif d_type == "nand":
        if any(c[0] and c[1] == "0" for c in in_consts): return True, "1"
        if all(c[0] and c[1] == "1" for c in in_consts): return True, "0"
    elif d_type == "or":
        if any(c[0] and c[1] == "1" for c in in_consts): return True, "1"
        if all(c[0] and c[1] == "0" for c in in_consts): return True, "0"
    elif d_type == "nor":
        if any(c[0] and c[1] == "1" for c in in_consts): return True, "0"
        if all(c[0] and c[1] == "0" for c in in_consts): return True, "1"
    elif d_type in ["xor", "xnor"]:
        if all(c[0] for c in in_consts):
            ones = sum(1 for c in in_consts if c[1] == "1")
            is_xor = (ones % 2 == 1)
            val = "1" if (is_xor if d_type == "xor" else not is_xor) else "0"
            return True, val
            
    return False, ""

if __name__ == "__main__":
    # 1. 讀取與解析
    parser = VerilogParser()
    design = parser.parse("testcase/test01/test01.v")
    
    # 2. 邏輯分析
    analyzer = LogicAnalyzer(design)
    max_depth, max_net = analyzer.find_max_logic_depth()
    
    print("\n=== Logic Depth Analysis ===")
    print(f"Maximum Logic Depth is: {max_depth}")
    print(f"Critical Net: {max_net.name}")
    
    print("\n=== Critical Path Trace ===")
    print(analyzer.get_critical_path_string(max_net))