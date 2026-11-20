# -*- coding: utf-8 -*-
import os
import sys
import shutil
import argparse
import yaml
import time
import httpx       
from google import genai
from google.genai import types
from parser import VerilogParser, LogicAnalyzer
import z3
import re
import fast_eda

# 🚀 終極修復：針對大型電路設定超高遞迴深度，防止 RecursionError
sys.setrecursionlimit(200000)

if len(sys.argv) > 1 and sys.argv[1].endswith('.txt'):
    prompt_path = sys.argv[1]
    prompt_dir = os.path.dirname(os.path.abspath(prompt_path))
    test_name = os.path.basename(prompt_dir)
    LOG_FILE_PATH = os.path.join(prompt_dir, f"{test_name}.log")
else:
    LOG_FILE_PATH = "output.log" # 防呆預設值
# ==========================================
# 🌟 神級補丁：HTTP 網路層無縫攔截與重試
# ==========================================
original_send = httpx.Client.send

def patched_send(self, request, **kwargs):
    max_retries = 10    # 🚀 增加至 10 次重試，應對頻繁的 429 錯誤
    base_delay = 5     # 基礎等待時間
    
    # 為了允許 httpx 重複發送同一個 request，必須強制將 stream 讀入記憶體
    if hasattr(request, 'read'):
        request.read()
        
    for attempt in range(max_retries + 1):
        try:
            response = original_send(self, request, **kwargs)
            
            # 攔截 429 (請求過於頻繁) 與 503 (伺服器忙碌)
            if response.status_code in (429, 503) and attempt < max_retries:
                # 使用指數退避 (Exponential Backoff) 增加成功率
                wait_time = base_delay * (1.5 ** attempt) 
                print(f"\n[System 底層攔截]: Google API 忙碌中 ({response.status_code})。嘗試第 {attempt+1} 次重試，等待 {wait_time:.1f} 秒...")
                
                if hasattr(response, 'read'):
                    response.read()
                time.sleep(wait_time)
                continue 
                
            return response
        except Exception as e:
            if attempt < max_retries:
                time.sleep(base_delay)
                continue
            raise e

# 綁定補丁到全域的 httpx
httpx.Client.send = patched_send

# ==========================================
# 0. 初始化 Gemini API 與全域變數
# ==========================================
current_design = None
current_analyzer = None
system_bypass_cache = {}  # ✨ 新增：系統代筆快取字典
agent_log_text = ""

prompt_dir = ""               # 記住 prompt.txt 在哪個資料夾
current_design_filepath = ""  # 記住剛讀進來的 .v 檔案完整路徑
chat = None # 稍後會由設定檔初始化
cpp_graph = None # 稍後會初始化 C++ 極速引擎的 Python 介面
last_added_buffer_count = 0

# def log_output(message, is_massive=False):
#     """僅印出訊息到終端機，不再把中間過程寫入 log 檔 (避免雜訊)"""
#     if is_massive or len(message) > 500000:
#         # 使用 sys.stdout.write 進行底層高速輸出
#         sys.stdout.write(message + "\n")
#         sys.stdout.flush() 
#     else:
#         print(message)
def log_output(message, is_massive=False):
    """僅印出訊息到終端機，過長時進行截斷以避免輸出檔案爆炸"""
    # 如果標記為巨量，或者字串長度超過 10 萬字元，就啟動截斷保護
    if len(message) > 100000:
        safe_msg = (
            message[:20000] + 
            f"\n\n... [系統保護：巨量文字已截斷 (MASSIVE OUTPUT TRUNCATED FOR TERMINAL)] ...\n" +
            f"... [原始字串總長度: {len(message)} 字元，僅顯示首尾各 2 萬字元] ...\n\n" +
            message[-20000:]
        )
        sys.stdout.write(safe_msg + "\n")
        sys.stdout.flush() 
    else:
        print(message)

def write_final_log(final_text):
    """只把最終的 #RESPONSE 答案卷寫進 agent_output.log"""
    # 這裡改用 "w" (覆寫模式)，確保每次執行這個測資時，Log 檔都是最新且乾淨的答案
    with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
        if len(final_text) > 100000:
            f.write(final_text[:20000]) # 只存前 2 萬字
            f.write("\n\n... [系統保護：巨量文字已截斷 (MASSIVE OUTPUT TRUNCATED FOR LOG FILE)] ...\n")
            f.write(f"... [已成功將 {len(final_text)} 字元的完整資料輸出至 Standard Output] ...\n\n")
            f.write(final_text[-20000:]) # 只存後 2 萬字
            f.write("\n")
        else:
            f.write(final_text + "\n")

# ==========================================
# 1. 智慧尋路導航 (新增這個輔助函數)
# ==========================================
def resolve_filepath(filepath: str) -> str:
    """自動推論出檔案的真實絕對路徑"""
    global prompt_dir
    
    # 如果 LLM 給的是絕對路徑，或是剛好在當前執行目錄下找得到，直接回傳
    if os.path.exists(filepath):
        return filepath
        
    # 如果找不到，強制去 prompt.txt 所在的資料夾 (prompt_dir) 尋找
    if 'prompt_dir' in globals() and prompt_dir:
        # LLM 可能會給 "test01.v" 或是 "testcase/test01/test01.v"
        # 我們只取檔名 (test01.v)，並跟 prompt 所在目錄組合
        basename = os.path.basename(filepath)
        candidate_path = os.path.join(prompt_dir, basename)
        return candidate_path
        
    return filepath # 真的找不到就原樣回傳，讓後面的 open() 報錯

# ==========================================
# 2. 更新版的 Agent 工具
# ==========================================
def read_design(filepath: str) -> str:
    """Load a gate-level Verilog netlist from a given file path."""
    global current_design, current_analyzer, current_design_filepath, cpp_graph
    
    # 使用智慧導航找真實路徑
    actual_path = resolve_filepath(filepath)
    log_output(f"> [System 執行工具]: read_design(filepath='{actual_path}')")
    
    try:
        parser = VerilogParser()
        current_design = parser.parse(actual_path)
        current_analyzer = LogicAnalyzer(current_design)
        # 紀錄成功的原始檔案路徑
        current_design_filepath = actual_path 
        # ✨ 將 Python 解析好的電路結構，轉灌進 C++ 引擎裡
        cpp_graph = fast_eda.FastGraph()
        
        # 🚀 關鍵修復：從 Gates 提取連線，徹底解決 Wire Alias 造成的斷層！
        for gate_name, gate_obj in current_design.gates.items():
            in_nets = [n.name for n in gate_obj.input_nets]
            for out_net in gate_obj.output_nets:
                # 確保每一顆邏輯閘的每一條輸出線，都被完美綁定！
                cpp_graph.add_net_driver(out_net.name, gate_name, in_nets)
        msg = f"Success! Loaded design '{current_design.name}' with {len(current_design.inputs)} inputs and {len(current_design.gates)} gates."
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
    except Exception as e:
        err_msg = f"Error loading design: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def write_design(filepath: str) -> str:
    """Write the current design to a specified output Verilog file path."""
    global current_design, current_design_filepath, prompt_dir
    
    # 1. 霸道攔截：不管 LLM 給什麼路徑，我們只提取最尾端的「檔名」(例如 test01_out.v)
    basename = os.path.basename(filepath)
    
    # 2. 強制綁定：把檔名與 prompt.txt 所在的資料夾硬生生接在一起
    if prompt_dir:
        output_path = os.path.join(prompt_dir, basename)
    else:
        output_path = basename
        
    log_output(f"> [System 執行工具]: write_design(目標='{output_path}')")
    
    if current_design is None or not current_design_filepath:
        err_msg = "Error: No design loaded yet. Cannot write."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        # 3. 安全建置資料夾：確保 dirname 不是空的才建立，避免崩潰
        out_dir = os.path.dirname(output_path)
        if out_dir: 
            os.makedirs(out_dir, exist_ok=True)
            
        # 4. 原封不動複製
        # shutil.copy2(current_design_filepath, output_path)
        if hasattr(current_design, "mark_bus_bit_boundaries"):
            current_design.mark_bus_bit_boundaries()
        verilog_code = current_design.to_verilog()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(verilog_code)
        msg = f"Success! The current design has been written to {output_path}."
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        err_msg = f"Error writing design: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

import subprocess

# (如果你想保留之前寫的 check_equivalence 等工具，可以貼在這裡，為了簡潔我先省略)
def analyze_logic_depth(start_net: str = "", end_net: str = "") -> str:
    """
    General tool to calculate logic depths in the circuit.
    - To find the maximum logic depth of the ENTIRE design and its critical path, leave both start_net and end_net empty.
    - To find the maximum logic depth (number of gates) between two specific nets, provide both 'start_net' and 'end_net'.
    """
    global current_analyzer, current_design
    log_output(f"> [System 執行工具]: analyze_logic_depth(start='{start_net}', end='{end_net}')")
    
    if current_analyzer is None or current_design is None:
        err_msg = "Error: No design loaded yet."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        # ==========================================
        # 情境 1：點對點最大深度 (指定起終點)
        # ==========================================
        if start_net and end_net:
            memo = {} # 動態規劃紀錄表
            
            def dfs_max_depth(current_net_name):
                if current_net_name == start_net:
                    return 0
                if current_net_name in memo:
                    return memo[current_net_name]
                    
                net = current_design.nets.get(current_net_name)
                if not net or not net.driver or net.driver == 'INPUT_PORT':
                    memo[current_net_name] = -1
                    return -1
                    
                gate = net.driver
                if 'dff' in gate.gate_type.lower():
                    memo[current_net_name] = -1
                    return -1
                    
                max_d = -1
                for in_net in gate.input_nets:
                    d = dfs_max_depth(in_net.name)
                    if d != -1:
                        max_d = max(max_d, d + 1)
                        
                memo[current_net_name] = max_d
                return max_d
                
            depth = dfs_max_depth(end_net)
            
            if depth == -1:
                msg = f"No combinational path found from '{start_net}' to '{end_net}'."
            else:
                msg = f"Success! The maximum logic depth from '{start_net}' to '{end_net}' is {depth} gates."
                
        # ==========================================
        # 情境 2：全域最大深度與 Critical Path (參數留白)
        # ==========================================
        elif not start_net and not end_net:
            max_depth, max_net = current_analyzer.find_max_logic_depth()
            path_str = current_analyzer.get_critical_path_string(max_net)
            msg = f"Success! Max logic depth of the entire design is {max_depth}.\nOne of the Critical Paths is:\n{path_str}"
            
        # ==========================================
        # 情境 3：防呆機制 (只給了一個參數)
        # ==========================================
        else:
            msg = "Error: You must provide either BOTH 'start_net' and 'end_net', or leave BOTH empty."
            
        log_output(f"  -> [工具回傳]: \n{msg}")
        return msg
        
    except Exception as e:
        err_msg = f"Error calculating depth: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def analyze_paths(start_net: str, end_net: str, avoid_net: str = "", find_all: bool = False) -> str:
    """
    General tool to find combinational paths between two nets.
    - To find a single path, set find_all=False.
    - To find ALL possible paths, set find_all=True.
    - To strictly avoid passing through a specific net, provide its name in 'avoid_net'.
    """
    global current_analyzer, current_design, cpp_graph, system_bypass_cache
    log_output(f"> [System 執行工具]: analyze_paths(start='{start_net}', end='{end_net}', avoid='{avoid_net}', find_all={find_all})")
    
    if current_analyzer is None or current_design is None:
        err_msg = "Error: No design loaded yet."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
    
    try:
        # ==========================================
        # 情境 1：只找單一路徑
        # ==========================================
        if not find_all:
            all_paths = cpp_graph.find_paths(start_net, end_net, avoid_net, 1, 8.0)
            if not all_paths:
                msg = f"No valid path found from {start_net} to {end_net} avoiding {avoid_net}." if avoid_net else f"No valid path found from {start_net} to {end_net}."
            else:
                msg = f"Path found:\nPath 1: {all_paths[0]}"
            log_output(f"  -> [工具回傳]: \n{msg}")
            return msg
            
        # ==========================================
        # 情境 2：找出所有路徑 (呼叫 C++ 極速引擎)
        # ==========================================
        else:
            import time
            start_calc_time = time.time()
            
            # 呼叫 C++ 引擎 (限制 100萬條, 8.0秒)
            all_paths = cpp_graph.find_paths(start_net, end_net, avoid_net, 1000000, 8.0)
            calc_duration = time.time() - start_calc_time
            
            if not all_paths:
                msg = f"No paths found from '{start_net}' to '{end_net}'"
                msg += f" avoiding '{avoid_net}'." if avoid_net else "."
                log_output(f"  -> [工具回傳]: \n{msg}")
                return msg
                
            total_paths = len(all_paths)
            
            if total_paths >= 1000000: warn_msg = f" (System stopped at 1,000,000 paths max limit)"
            elif calc_duration >= 7.5: warn_msg = f" (System stopped due to time limit, found {total_paths} paths before timeout)"
            else: warn_msg = "" 
                
            # 1. 產生截斷版清單 (最多 30 行)
            massive_str_list = [f"Path {i+1}: {p}" for i, p in enumerate(all_paths)]
            if total_paths > 30:
                paths_to_show = massive_str_list[:15] + [f"... [truncated {total_paths - 30} paths] ..."] + massive_str_list[-15:]
            else:
                paths_to_show = massive_str_list
                
            paths_str = "\n".join(paths_to_show)
            
            # 2. 🚀 將截斷版清單塞入「兌換券」中，強制代筆！
            bypass_key = f"<<SYSTEM_BYPASS_PATHS_{start_net}_TO_{end_net}>>"
            massive_result = f"Found {total_paths} paths{warn_msg}:\n{paths_str}"
            system_bypass_cache[bypass_key] = massive_result
            
            # 3. 恐嚇 LLM 只能印出這張兌換券
            msg = f"Success! Found {total_paths} paths{warn_msg}.\n"
            msg += f"To prevent context overflow, use this token to output the exact paths:\n{bypass_key}\n"
            msg += f"CRITICAL: You MUST output the above token EXACTLY as your answer for this task."
                
            log_output(f"  -> [工具回傳]: \n{msg}")
            return msg
        
    except Exception as e:
        err_msg = f"Error calculating paths: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
       
def analyze_logic_cone(net_name: str, direction: str, threshold: int = -1) -> str:
    """
    General tool to analyze the logic cone of a specific point.
    - 'net_name' can be EITHER a net/wire name (e.g., 'n6') OR a gate instance name (e.g., 'g0').
    - 'direction' must be 'in' (for fan-in) or 'out' (for fan-out).
    - 'threshold' limits the depth. Use -1 for infinite depth.
    """
    global current_analyzer, current_design
    
    # 紀錄 LLM 傳入了什麼參數
    log_output(f"> [System 執行工具]: analyze_logic_cone(net_name='{net_name}', direction='{direction}', threshold={threshold})")
    
    if current_analyzer is None or current_design is None:
        err_msg = "Error: No design loaded."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    results = []
    
    try:
        actual_net_name = net_name
        is_gate = False # ✨ 新增標記：用來優化回傳給 LLM 的字串
        
        # 自動辨識 Gate 並轉換為其輸出的 Net
        if actual_net_name not in current_design.nets and actual_net_name in current_design.gates:
            is_gate = True
            # 加入 hasattr(obj.driver, 'inst_name') 來過濾掉 'INPUT_PORT' 這名字串
            driven_nets = [
                n for n, obj in current_design.nets.items() 
                if hasattr(obj.driver, 'inst_name') and obj.driver.inst_name == actual_net_name
            ]
            
            if driven_nets:
                actual_net_name = driven_nets[0] 
            else:
                msg = f"Success!\nGate '{net_name}' does not drive any wires."
                log_output(f"  -> [工具回傳]: \n{msg}")
                return msg

        # ==========================================
        # 情境 1：全域篩選 (找出大於 Threshold 的 Output)
        # ==========================================
        if threshold != -1:
            over_threshold = []
            for out_net in current_design.outputs:
                if current_analyzer.get_logic_cone_size(out_net) > threshold:
                    over_threshold.append(out_net)
            if over_threshold:
                results.append(f"Outputs with fan-in cone > {threshold}: {', '.join(over_threshold)}")
            else:
                results.append(f"No outputs have a fan-in cone larger than {threshold}.")
                
        # ==========================================
        # 情境 2：單點查詢 (查詢特定線段的 Fan-in 或 Fan-out)
        # ==========================================
        if net_name:
            entity_type = "Gate" if is_gate else "Net"
            
            if direction.lower() == "in":
                # 往回找 Fan-in (⚠️ 關鍵修正：這裡必須傳入 actual_net_name)
                size = current_analyzer.get_logic_cone_size(actual_net_name)
                if size != -1:
                    results.append(f"The fan-in logic cone of {entity_type} '{net_name}' contains {size} gates.")
                else:
                    results.append(f"Error: {entity_type} '{actual_net_name}' not found.")
                    
            elif direction.lower() == "out":
                # ==========================================
                # 🚀 升級：支援 Transitive Fan-out 的 BFS 追蹤
                # ==========================================
                
                # 1. 建立正向查找表 (O(N) 複雜度，避免每次都跑巢狀迴圈卡死)
                forward_map = {}
                for inst_name, gate in current_design.gates.items():
                    for in_net in gate.input_nets:
                        if in_net.name not in forward_map:
                            forward_map[in_net.name] = []
                        forward_map[in_net.name].append(gate)
                
                # 2. BFS 初始化
                visited_gates = set()
                visited_nets = set([actual_net_name])
                # Queue 儲存狀態: (線網名稱, 當前深度)
                queue = [(actual_net_name, 0)]
                immediate_gates = [] # 紀錄第一層，方便 threshold=1 時印出名單
                
                # 3. 開始 BFS 走訪 (遞迴展開所有後續影響的 gate)
                while queue:
                    curr_net, depth = queue.pop(0)
                    
                    # 如果有設定 threshold (例如 threshold=1 代表只要 immediate)，超過深度就不再往下找
                    if threshold != -1 and depth >= threshold:
                        continue
                        
                    if curr_net in forward_map:
                        for gate in forward_map[curr_net]:
                            if gate.inst_name not in visited_gates:
                                visited_gates.add(gate.inst_name)
                                
                                # 記錄第一層的詳細名單
                                if depth == 0:
                                    immediate_gates.append(f"{gate.inst_name}({gate.gate_type})")
                                
                                # 將這顆邏輯閘輸出的所有線網加入 Queue，繼續往下挖！
                                for out_net in gate.output_nets:
                                    if out_net.name not in visited_nets:
                                        visited_nets.add(out_net.name)
                                        queue.append((out_net.name, depth + 1))
                                        
                # 4. 根據 BFS 結果與 User 要求的深度產生回報
                if visited_gates:
                    # 如果只要找第一層 immediate fan-out (threshold == 1)
                    if threshold == 1:
                        results.append(f"{entity_type} '{net_name}' drives {len(immediate_gates)} gates: {', '.join(immediate_gates)}")
                    # 如果要找 transitive fan-out cone (threshold == -1)
                    else:
                        results.append(f"The transitive fan-out logic cone of {entity_type} '{net_name}' contains {len(visited_gates)} gates.")
                else:
                    results.append(f"{entity_type} '{net_name}' does not drive any gates (fanout is 0).")
                
        # ==========================================
        # 結果回傳
        # ==========================================
        if not results:
            msg = "Error: You must provide either 'net_name' or 'threshold'."
        else:
            msg = "Success!\n" + "\n".join(results)
            
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        err_msg = f"Error during analysis: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def check_equivalence(net1: str, net2: str = "") -> str:
    """
    Check functional equivalence between two internal signals or primary outputs.
    If net2 is omitted, checks if net1 is always 0.
    """
    global current_design
    log_output(f"> [System 執行工具]: check_equivalence('{net1}', '{net2}') -> 呼叫 Yosys SAT 引擎")
    if current_design is None: return "Error: No design loaded."
    
    try:
        import os, subprocess, tempfile
        from parser import Gate
        
        n1 = current_design.nets.get(net1)
        if not n1: return f"Error: Net {net1} not found."
        
        # 🌟 動態掛載測試點 (Test Point)
        # 如果要證明 n1 == n2，就把它們接上 XOR 閘，證明輸出永遠為 0
        dummy_gate = Gate("yosys_dummy_probe", "xor" if net2 else "buf")
        dummy_out = current_design.get_or_create_net("yosys_sat_target")
        
        dummy_gate.input_nets.append(n1)
        n1.loads.append(dummy_gate)
        
        if net2:
            n2 = current_design.nets.get(net2)
            if not n2: return f"Error: Net {net2} not found."
            dummy_gate.input_nets.append(n2)
            n2.loads.append(dummy_gate)
            
        dummy_gate.output_nets.append(dummy_out)
        dummy_out.driver = dummy_gate
        current_design.gates["yosys_dummy_probe"] = dummy_gate
        current_design.outputs.append("yosys_sat_target")
        
        # 導出包含了測試點的 Verilog
        verilog_code = current_design.to_verilog()
        
        # 🌟 立即拔除測試點 (神不知鬼不覺，無痕恢復 AST)
        current_design.outputs.remove("yosys_sat_target")
        del current_design.gates["yosys_dummy_probe"]
        n1.loads.remove(dummy_gate)
        if net2: n2.loads.remove(dummy_gate)
        del current_design.nets["yosys_sat_target"]
        
        # 呼叫 Yosys 進行硬核 SAT 求解
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.v') as tmp:
            tmp.write(verilog_code)
            tmp_path = tmp.name
            
        yosys_cmd = f"read_verilog {tmp_path}; prep -top top; sat -verify -prove yosys_sat_target 0"
        result = subprocess.run(["yosys", "-p", yosys_cmd], capture_output=True, text=True)
        os.remove(tmp_path)
        
        is_proven = "Successfully proved" in result.stdout or "Proof successful" in result.stdout or "proved" in result.stdout.lower()
        
        if net2:
            msg = f"Result: '{net1}' and '{net2}' are {'functionally equivalent' if is_proven else 'NOT equivalent'}."
        else:
            msg = f"Result: '{net1}' is {'ALWAYS 0' if is_proven else 'NOT always 0'} regardless of inputs."
            
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        import traceback
        return f"Error running Yosys SAT: {str(e)}\n{traceback.format_exc()}"


def report_max_depth_to_dff() -> str:
    """Calculate the maximum logic depth to any Primary Output or DFF D-pin, and identify the deepest net."""
    global current_design
    log_output("> [System 執行工具]: report_max_depth_to_dff()")
    if current_design is None: return "Error: No design loaded."

    try:
        # 初始化所有起點深度為 0 (Primary Inputs & DFF Q pins)
        net_depths = {inp: 0 for inp in current_design.inputs}
        for inst, gate in current_design.gates.items():
            if gate.gate_type.strip().lower() == 'dff':
                for out_net in gate.output_nets:
                    net_depths[out_net.name] = 0

        # 動態規劃 (鬆弛演算法) 計算深度
        changed = True
        iters = 0
        while changed and iters < 1000:
            changed = False
            iters += 1
            for inst, gate in current_design.gates.items():
                if gate.gate_type.strip().lower() == 'dff': continue
                
                max_in_depth = -1
                for in_net in gate.input_nets:
                    if in_net.name in net_depths:
                        max_in_depth = max(max_in_depth, net_depths[in_net.name])

                if max_in_depth >= 0:
                    out_depth = max_in_depth + 1
                    for out_net in gate.output_nets:
                        if out_net.name not in net_depths or net_depths[out_net.name] < out_depth:
                            net_depths[out_net.name] = out_depth
                            changed = True

        # 🌟 升級點 1：同時記錄「最大深度」、「最深節點名稱」、「節點類型」
        max_depth = -1
        deepest_node = ""
        node_type = ""

        # 1. 檢查所有 DFF 的 D 端
        for inst, gate in current_design.gates.items():
            if gate.gate_type.strip().lower() == 'dff':
                d_net = gate.input_nets[0].name if gate.input_nets else None
                if getattr(gate, "port_mapping", None):
                    for port, net_name in gate.port_mapping.items():
                        if port.lower() in ['d', 'data', 'din']: d_net = net_name
                        
                if d_net and d_net in net_depths:
                    if net_depths[d_net] > max_depth:
                        max_depth = net_depths[d_net]
                        deepest_node = d_net
                        node_type = f"DFF D-pin (Gate: {inst})"

        # 🌟 升級點 2：絕對不能忘記檢查 Primary Outputs (PO)！
        for po in current_design.outputs:
            if po in net_depths:
                if net_depths[po] > max_depth:
                    max_depth = net_depths[po]
                    deepest_node = po
                    node_type = "Primary Output"

        # 🌟 升級點 3：把具體名字一起回傳給 LLM，打破它的工具盲區
        msg = f"The maximum logic depth of the design is {max_depth}. The specific output bit or D-pin with this deepest fanin logic cone is '{deepest_node}' ({node_type})."
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        err_msg = f"Error calculating max depth: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg


def count_outputs_depth_greater_than(threshold: int) -> str:
    """Count how many primary output bits have a logic depth strictly greater than the specified threshold."""
    global current_design

    log_output(f"> [System 執行工具]: count_outputs_depth_greater_than({threshold})")

    if current_design is None:
        return "Error: No design loaded."

    try:
        # ============================================================
        # 1. 展開 Primary Inputs
        # ============================================================
        if hasattr(current_design, "get_expanded_inputs"):
            input_names = current_design.get_expanded_inputs()
        else:
            input_names = current_design.inputs

        net_depths = {inp: 0 for inp in input_names}
        # ============================================================
        # 2. 常數 nets depth = 0
        # ============================================================
        for const_net in ["1'b0", "1'b1", "1'bx", "1'bz", "1'h0", "1'h1", "0", "1"]:
            net_depths[const_net] = 0
        # ============================================================
        # 3. DFF Q outputs 視為 sequential boundary，depth = 0
        # ============================================================
        dff_count = 0

        for inst, gate in current_design.gates.items():
            if gate.gate_type.strip().lower() == 'dff':
                dff_count += 1
                for out_net in gate.output_nets:
                    net_depths[out_net.name] = 0
        # ============================================================
        # 4. 傳播 combinational logic depth
        # ============================================================
        changed = True
        iters = 0
        max_iters = 1000

        while changed and iters < max_iters:
            changed = False
            iters += 1

            for inst, gate in current_design.gates.items():
                if gate.gate_type.strip().lower() == 'dff':
                    continue

                if not gate.input_nets:
                    continue

                input_depths = []
                all_inputs_known = True

                for in_net in gate.input_nets:
                    in_name = in_net.name

                    if in_name not in net_depths:
                        all_inputs_known = False
                        break

                    input_depths.append(net_depths[in_name])

                if not all_inputs_known:
                    continue

                out_depth = max(input_depths) + 1

                for out_net in gate.output_nets:
                    out_name = out_net.name

                    if out_name not in net_depths or net_depths[out_name] < out_depth:
                        net_depths[out_name] = out_depth
                        changed = True

        if changed:
            log_output(
                f"  -> [警告]: 深度計算達到 {max_iters} 次迭代仍未收斂，"
                f"可能存在 combinational loop。"
            )
        # ============================================================
        # 5. 展開 Primary Outputs
        # ============================================================
        if hasattr(current_design, "get_expanded_outputs"):
            output_names = current_design.get_expanded_outputs()
        else:
            output_names = current_design.outputs
        # ============================================================
        # 6. Floating primary outputs 視為 depth 0
        # ============================================================
        floating_outputs = []

        for out in output_names:
            net_obj = current_design.nets.get(out)

            if net_obj is None:
                continue

            is_primary_output = "OUTPUT_PORT" in net_obj.loads

            if is_primary_output and net_obj.driver is None:
                net_depths[out] = 0
                floating_outputs.append(out)

        if floating_outputs:
            log_output(
                f"  -> [警告]: {len(floating_outputs)} 個 primary output 是 floating，"
                f"已視為 depth 0，不計入 violation。"
            )
            log_output(f"  -> [DEBUG]: floating outputs = {floating_outputs[:30]}")
        # ============================================================
        # 7. 統計 violating outputs
        # ============================================================
        violating_outputs = 0
        unresolved_outputs = []

        for out in output_names:
            if out in net_depths:
                if net_depths[out] > threshold:
                    violating_outputs += 1
            else:
                unresolved_outputs.append(out)

        if unresolved_outputs:
            log_output(f"  -> [警告]: 仍有 {len(unresolved_outputs)} 個 output bits 無法解析。")
            log_output(f"  -> [DEBUG]: unresolved output bits sample = {unresolved_outputs[:30]}")

        # ============================================================
        # 8. AutoGrader 純答案
        # ============================================================
        ans = f"There are {violating_outputs} output bits with a logic depth greater than {threshold}."

        log_output(f"  -> [工具回傳]: {ans}")

        return ("Extract ONLY the exact text inside the XML tags and put it in your response:\n""<ANSWER>\n"f"{ans}\n""</ANSWER>")

    except Exception as e:
        import traceback
        err_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def find_equivalent_signal(target_net: str, inverted: bool = False) -> str:
    global current_design, current_analyzer
    log_output(f"> [System 執行工具]: find_equivalent_signal(target='{target_net}', inverted={inverted})")
    
    if current_analyzer is None or current_design is None:
        err = "Error: No design loaded yet."
        log_output(f"  -> [工具內部報錯]: {err}")
        return err
        
    try:
        z3_vars = {}
        visited_expr = {}
        target_expr = current_analyzer.build_z3_expr(target_net, z3_vars, visited_expr)
        
        if inverted:
            target_expr = z3.Not(target_expr)
            
        found_nets = []
        for net_name in current_design.nets:
            if net_name == target_net and not inverted:
                continue
                
            test_expr = current_analyzer.build_z3_expr(net_name, z3_vars, visited_expr)
            s = z3.Solver()
            s.add(target_expr != test_expr)
            
            if s.check() == z3.unsat:
                found_nets.append(net_name)
                
        if found_nets:
            condition = f"NOT({target_net})" if inverted else target_net
            res = f"Success! Found signals logically equivalent to {condition}: {', '.join(found_nets)}"
        else:
            res = "No logically equivalent signals found in the design."
            
        log_output(f"  -> [工具回傳]: \n{res}")
        return res
            
    except Exception as e:
        err = f"Error during search: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err}")
        return err

def find_equivalent_combination(target_net: str, gate_type: str) -> str:
    """
    Search for a pair of internal signals (a, b) such that gate_type(a, b) is equivalent to target_net.
    🚀 [極速版]: 2048-bit Simulation + Mathematical Pruning + Z3 Formal Verification.
    """
    global current_design, current_analyzer
    log_output(f"> [System 執行工具]: find_equivalent_combination(target='{target_net}', gate='{gate_type}')")
    
    if current_analyzer is None or current_design is None:
        return "Error: No design loaded yet."
        
    try:
        import random
        g_type = gate_type.upper()
        
        # 🌟 升級 1：使用 2048-bit 超高解析度隨機模擬，讓假陽性機率降到 0！
        SIM_BITS = 2048
        mask = (1 << SIM_BITS) - 1
        
        signatures = {}
        depths = current_design.get_logic_depths()
        sorted_nets = sorted(current_design.nets.keys(), key=lambda n: depths.get(n, 0))
        
        # 1. 產生 2048-bit 簽名
        for net_name in sorted_nets:
            net_obj = current_design.nets[net_name]
            
            if net_obj.driver == 'INPUT_PORT' or (hasattr(net_obj.driver, 'gate_type') and net_obj.driver.gate_type == 'dff'):
                signatures[net_name] = random.getrandbits(SIM_BITS)
            elif net_name in ["1'b0", "1'h0", "0"]:
                signatures[net_name] = 0
            elif net_name in ["1'b1", "1'h1", "1"]:
                signatures[net_name] = mask
            elif net_obj.driver is None:
                signatures[net_name] = random.getrandbits(SIM_BITS)
            else:
                gate = net_obj.driver
                in_sigs = [signatures.get(n.name, 0) for n in gate.input_nets]
                
                if gate.gate_type == 'and': 
                    res = in_sigs[0]
                    for s in in_sigs[1:]: res &= s
                elif gate.gate_type == 'or':
                    res = in_sigs[0]
                    for s in in_sigs[1:]: res |= s
                elif gate.gate_type == 'not': res = ~in_sigs[0] & mask
                elif gate.gate_type == 'nand':
                    res = in_sigs[0]
                    for s in in_sigs[1:]: res &= s
                    res = ~res & mask
                elif gate.gate_type == 'nor':
                    res = in_sigs[0]
                    for s in in_sigs[1:]: res |= s
                    res = ~res & mask
                elif gate.gate_type == 'xor': res = in_sigs[0] ^ in_sigs[1]
                elif gate.gate_type == 'xnor': res = ~(in_sigs[0] ^ in_sigs[1]) & mask
                elif gate.gate_type == 'buf': res = in_sigs[0]
                else: res = random.getrandbits(SIM_BITS)
                
                signatures[net_name] = res
        
        if target_net not in signatures:
            return f"Error: Target net '{target_net}' not found in signatures."
            
        target_sig = signatures[target_net]
        inv_target_sig = (~target_sig) & mask
        
        # 🌟 升級 2：O(N) 數學剪枝 (Mathematical Pruning) 與 Hash Lookup
        all_nets = list(current_design.nets.keys())
        potential_pairs = []
        
        # 對 XOR / XNOR 使用絕對精準的 Hash 查表 (時間複雜度降為 O(N))
        if g_type in ['XOR', 'XNOR']:
            sig_map = {}
            for n in all_nets:
                sig_map.setdefault(signatures[n], []).append(n)
            
            req_xor = target_sig if g_type == 'XOR' else inv_target_sig
            for a_name in all_nets:
                s_b_needed = signatures[a_name] ^ req_xor
                if s_b_needed in sig_map:
                    for b_name in sig_map[s_b_needed]:
                        if a_name <= b_name: # 避免 (a,b) 與 (b,a) 重複
                            potential_pairs.append((a_name, b_name))
        else:
            # 對 AND/OR/NAND/NOR 使用布林特徵剪枝，將 O(N^2) 運算量砍掉 99%
            candidate_nets = []
            for n in all_nets:
                sig = signatures[n]
                if g_type == 'AND' and (sig & target_sig) == target_sig: candidate_nets.append(n)
                elif g_type == 'OR' and (sig | target_sig) == target_sig: candidate_nets.append(n)
                elif g_type == 'NAND' and (sig & inv_target_sig) == inv_target_sig: candidate_nets.append(n)
                elif g_type == 'NOR' and (sig | inv_target_sig) == inv_target_sig: candidate_nets.append(n)

            log_output(f"  -> [System 剪枝]: 透過特徵篩選，候選節點從 {len(all_nets)} 驟降至 {len(candidate_nets)}。")

            # 僅在極小量的 candidate 中進行配對
            for i in range(len(candidate_nets)):
                for j in range(i, len(candidate_nets)):
                    a_name, b_name = candidate_nets[i], candidate_nets[j]
                    s1, s2 = signatures[a_name], signatures[b_name]
                    
                    if g_type == 'AND' and (s1 & s2) == target_sig: potential_pairs.append((a_name, b_name))
                    elif g_type == 'OR' and (s1 | s2) == target_sig: potential_pairs.append((a_name, b_name))
                    elif g_type == 'NAND' and (~(s1 & s2) & mask) == target_sig: potential_pairs.append((a_name, b_name))
                    elif g_type == 'NOR' and (~(s1 | s2) & mask) == target_sig: potential_pairs.append((a_name, b_name))

        log_output(f"  -> [System 模擬]: 透過 2048-bit 模擬過濾出 {len(potential_pairs)} 組完美配對。")
        
        if not potential_pairs:
            msg = f"No such pair of signals found for {g_type}(a, b) == {target_net}."
            log_output(f"  -> [工具回傳]: {msg}")
            return msg

        # 防呆機制：若配對數量異常多，強制截斷避免 Z3 卡死
        if len(potential_pairs) > 20:
            log_output(f"  -> [警告]: 匹配數量達到 {len(potential_pairs)}，僅將前 20 組送入 Z3 驗證。")
            potential_pairs = potential_pairs[:20]

        # 3. 使用 Z3 進行最後的正體驗證
        z3_vars = {}
        visited_expr = {}
        target_expr = current_analyzer.build_z3_expr(target_net, z3_vars, visited_expr)
        
        def combine_exprs(e1, e2, g):
            if g == 'AND': return z3.And(e1, e2)
            if g == 'OR': return z3.Or(e1, e2)
            if g == 'NAND': return z3.Not(z3.And(e1, e2))
            if g == 'NOR': return z3.Not(z3.Or(e1, e2))
            if g == 'XOR': return z3.Xor(e1, e2)
            if g == 'XNOR': return z3.Not(z3.Xor(e1, e2))
            return None

        for a_name, b_name in potential_pairs:
            a_expr = current_analyzer.build_z3_expr(a_name, z3_vars, visited_expr)
            b_expr = current_analyzer.build_z3_expr(b_name, z3_vars, visited_expr)
            
            combined = combine_exprs(a_expr, b_expr, g_type)
            s = z3.Solver()
            s.set("timeout", 2000) 
            s.add(combined != target_expr) # 尋找反例
            
            if s.check() == z3.unsat: # 若無反例，代表 100% 等價
                msg = f"Success! Found a pair ({a_name}, {b_name}) already in the netlist such that {g_type}({a_name}, {b_name}) is equivalent to {target_net}."
                log_output(f"  -> [工具回傳]: {msg}")
                return msg
                
        return f"No valid pairs passed formal verification for {g_type}(a, b) == {target_net}."
        
    except Exception as e:
        import traceback
        err_msg = f"Error during equivalent combination search: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg


def check_clock_domain(dff1: str, dff2: str) -> str:
    """Check if two DFFs (flip-flops) belong to the same clock domain."""
    global current_analyzer
    log_output(f"> [System 執行工具]: check_clock_domain(dff1='{dff1}', dff2='{dff2}')")
    
    if current_analyzer is None:
        err = "Error: No design loaded yet."
        log_output(f"  -> [工具內部報錯]: {err}")
        return err
        
    try:
        res = current_analyzer.check_same_clock_domain(dff1, dff2)
        log_output(f"  -> [工具回傳]: \n{res}")
        return res
    except Exception as e:
        err = f"Error checking clock domains: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err}")
        return err

def check_property(done_net: str, req_net: str, busy_net: str) -> str:
    global current_analyzer
    log_output(f"> [System 執行工具]: check_property(done='{done_net}', req='{req_net}', busy='{busy_net}')")
    
    if current_analyzer is None:
        err = "Error: No design loaded yet."
        log_output(f"  -> [工具內部報錯]: {err}")
        return err
        
    try:
        res = current_analyzer.verify_specific_property(done_net, req_net, busy_net)
        log_output(f"  -> [工具回傳]: \n{res}")
        return res
    except Exception as e:
        err = f"Error checking property: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err}")
        return err

def report_gate_types(target_net: str = "") -> str:
    """Count all the gates in the current design (or within a specific fanin cone) and report the total count broken down by gate type."""
    global current_design
    if target_net:
        log_output(f"> [System 執行工具]: report_gate_types(cone='{target_net}')")
    else:
        log_output("> [System 執行工具]: report_gate_types()")
    
    if current_design is None:
        err_msg = "Error: No design loaded yet."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        gate_counts = {}
        if not target_net:
            for inst_name, gate in current_design.gates.items():
                g_type = gate.gate_type.upper()
                gate_counts[g_type] = gate_counts.get(g_type, 0) + 1
        else:
            net_name = target_net
            if net_name in current_design.nets:
                net = current_design.nets[net_name]
                if net.driver and net.driver.gate_type.lower() == 'dff':
                    dff_gate = net.driver
                    if getattr(dff_gate, "port_mapping", None):
                        d_net = dff_gate.port_mapping.get('D') or dff_gate.port_mapping.get('d')
                        if d_net:
                            net_name = d_net
                    else:
                        for in_net in dff_gate.input_nets:
                            if 'clk' not in in_net.name.lower() and 'rst' not in in_net.name.lower() and in_net.name not in ["1'b0", "1'b1"]:
                                net_name = in_net.name
                                break
                            
            if net_name not in current_design.nets:
                return f"Error: Net '{target_net}' not found."
                
            visited_gates = set()
            queue = [current_design.nets[net_name]]
            while queue:
                current_net = queue.pop(0)
                driver_gate = current_net.driver
                if driver_gate and driver_gate != 'INPUT_PORT' and driver_gate.inst_name not in visited_gates:
                    visited_gates.add(driver_gate.inst_name)
                    g_type = driver_gate.gate_type.upper()
                    gate_counts[g_type] = gate_counts.get(g_type, 0) + 1
                    
                    if 'dff' in driver_gate.gate_type.lower():
                        continue
                    for in_net in driver_gate.input_nets:
                        queue.append(in_net)
            
        breakdown_list = [f"{g_type}: {count}" for g_type, count in gate_counts.items()]
        breakdown_str = ", ".join(breakdown_list)
        
        msg = f"Success! Gate breakdown: {breakdown_str}"
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        err_msg = f"Error counting gate types: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def list_gates_of_type(target_type: str, target_cone: str = "") -> str:
    """List all gates of a specific type with their connections."""
    global current_design
    log_output(f"> [System 執行工具]: list_gates_of_type(type='{target_type}', cone='{target_cone}')")
    
    if current_design is None:
        return "Error: No design loaded yet."
        
    try:
        target_type = target_type.strip().lower()
        found_gates = []
        
        # Determine the set of gates to check
        if not target_cone:
            gates_to_check = current_design.gates.values()
        else:
            net_name = target_cone
            if net_name in current_design.nets:
                net = current_design.nets[net_name]
                if net.driver and getattr(net.driver, 'gate_type', '').lower() == 'dff':
                    dff_gate = net.driver
                    if getattr(dff_gate, "port_mapping", None):
                        d_net = dff_gate.port_mapping.get('D') or dff_gate.port_mapping.get('d')
                        if d_net: net_name = d_net
                    else:
                        for in_net in dff_gate.input_nets:
                            if 'clk' not in in_net.name.lower() and 'rst' not in in_net.name.lower() and in_net.name not in ["1'b0", "1'b1"]:
                                net_name = in_net.name
                                break
            
            if net_name not in current_design.nets:
                return f"Error: Net '{target_cone}' not found."
                
            visited_gates = set()
            gates_to_check = []
            queue = [current_design.nets[net_name]]
            while queue:
                current_net = queue.pop(0)
                driver_gate = current_net.driver
                if driver_gate and driver_gate != 'INPUT_PORT' and driver_gate.inst_name not in visited_gates:
                    visited_gates.add(driver_gate.inst_name)
                    gates_to_check.append(driver_gate)
                    if driver_gate.gate_type.lower() != 'dff':
                        for in_net in driver_gate.input_nets:
                            queue.append(in_net)

        # Filter and format
        for gate in gates_to_check:
            if gate.gate_type.lower() == target_type:
                ins = [n.name for n in gate.input_nets]
                outs = [n.name for n in gate.output_nets]
                found_gates.append(f"  - {gate.inst_name}: Inputs = {ins}, Outputs = {outs}")
        
        count = len(found_gates)
        if count == 0:
            res = f"No {target_type.upper()} gates found" + (f" in cone of {target_cone}." if target_cone else ".")
        else:
            res = f"Found {count} {target_type.upper()} gates" + (f" in cone of {target_cone}:" if target_cone else ":") + "\n"
            if count > 50:
                res += "\n".join(found_gates[:25]) + "\n  ... [truncated] ...\n" + "\n".join(found_gates[-25:])
            else:
                res += "\n".join(found_gates)
                
        log_output(f"  -> [工具回傳]: \n{res}")
        return res
    except Exception as e:
        return f"Error: {str(e)}"

def report_gate_info(gate_name: str) -> str:
    """Get the type and pin connections of a specific gate."""
    global current_design
    log_output(f"> [System 執行工具]: report_gate_info('{gate_name}')")
    if current_design is None: return "Error: No design loaded."
    
    gate = current_design.gates.get(gate_name)
    if not gate: return f"Error: Gate '{gate_name}' not found."
    
    ins = [n.name for n in gate.input_nets]
    outs = [n.name for n in gate.output_nets]
    msg = f"Gate {gate_name}: Type = {gate.gate_type}, Inputs = {ins}, Outputs = {outs}"
    log_output(f"  -> [工具回傳]: {msg}")
    return msg

def report_net_fanout(target_net: str) -> str:
    """List every gate that a specific net drives directly (Direct Fanout)."""
    global current_design
    log_output(f"> [System 執行工具]: report_net_fanout('{target_net}')")
    if current_design is None: return "Error: No design loaded."
    
    net = current_design.nets.get(target_net)
    if not net: return f"Error: Net '{target_net}' not found."
    
    loads = []
    for ld in net.loads:
        if type(ld) == str: loads.append(ld)
        else: loads.append(f"{ld.inst_name}({ld.gate_type})")
            
    count = len(loads)
    if count > 30:
        loads_str = f"{', '.join(loads[:15])}, ... [truncated {count-30} gates] ..., {', '.join(loads[-15:])}"
    else:
        loads_str = ', '.join(loads)
            
    # 🌟 絕對純淨
    return f"Fanout of {target_net} is {count}. It directly drives: {loads_str}"


def report_reachable_gates(target_net: str) -> str:
    """Determine all gates reachable from a specific net (Transitive Fanout / Forward Tracing)."""
    global current_design
    log_output(f"> [System 執行工具]: report_reachable_gates('{target_net}')")
    if current_design is None: return "Error: No design loaded."
    
    net = current_design.nets.get(target_net)
    if not net: return f"Error: Net '{target_net}' not found."
    
    visited_gates = set()
    queue = [net]
    
    while queue:
        curr_net = queue.pop(0)
        for load in curr_net.loads:
            if type(load) == str: continue
            if load.inst_name not in visited_gates:
                visited_gates.add(load.inst_name)
                for out_n in load.output_nets:
                    queue.append(out_n)
                    
    sorted_gates = sorted(list(visited_gates))
    count = len(sorted_gates)
    
    if count > 30:
        gate_str = f"{', '.join(sorted_gates[:15])}, ... [truncated {count-30} gates] ..., {', '.join(sorted_gates[-15:])}"
    else:
        gate_str = ', '.join(sorted_gates)
                    
    # 🌟 絕對純淨
    return f"Gates reachable from {target_net} (Total Count: {count}): {gate_str}"


def report_shared_fanin(net1: str, net2: str) -> str:
    """Report all combinational gates shared between the fanin cones of two nets."""
    global current_design
    log_output(f"> [System 執行工具]: report_shared_fanin('{net1}', '{net2}')")
    if current_design is None: return "Error: No design loaded."
    
    def get_fanin_cone(start_net):
        visited = set()
        queue = [start_net]
        while queue:
            curr = queue.pop(0)
            n_obj = current_design.nets.get(curr)
            if not n_obj or not n_obj.driver or type(n_obj.driver) == str: continue
            gate = n_obj.driver
            if gate.gate_type.strip().lower() == 'dff': continue
            
            if gate.inst_name not in visited:
                visited.add(gate.inst_name)
                for in_n in gate.input_nets:
                    queue.append(in_n.name)
        return visited
        
    cone1 = get_fanin_cone(net1)
    cone2 = get_fanin_cone(net2)
    shared = sorted(list(cone1.intersection(cone2)))
    count = len(shared)
    
    if count > 30:
        shared_str = f"{', '.join(shared[:15])}, ... [truncated {count-30} gates] ..., {', '.join(shared[-15:])}"
    else:
        shared_str = ', '.join(shared)
    
    # 🌟 絕對純淨
    return f"Shared gates in fanin cones of {net1} and {net2} (Count: {count}): {shared_str}"

def derive_boolean_equation(target_net: str) -> str:
    """
    Derive the Boolean equation for an output or internal net using recursive traversal.

    Behavior:
    - For pure combinational nets:
        returns target = expression(primary inputs)
    - For DFF Q outputs:
        reports current-state equation and next-state equation.
    - If DFF boundaries are encountered while deriving:
        expression is in terms of primary inputs AND state variables.
    - Stops at:
        primary inputs,
        DFF Q outputs,
        undriven nets,
        recursion/cycle boundary.
    """
    global current_design

    log_output(f"> [System 執行工具]: derive_boolean_equation('{target_net}')")

    if current_design is None:
        return "Error: No design loaded."

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def base_name(name: str) -> str:
        name = str(name).replace("\\", "").strip()
        return name.split("[")[0]


    def is_const_net(name: str) -> bool:
        name = str(name).strip()
        return (
            name in ["1'b0", "1'b1", "1'bx", "1'bz", "0", "1"]
            or "'" in name
        )

    def is_primary_input_name(name: str) -> bool:
        return name in current_design.inputs or base_name(name) in current_design.inputs

    def get_dff_d_net_name(dff_gate):
        """
        Return D input net name from DFF port_mapping if available.
        Supports D/d.
        """
        if hasattr(dff_gate, "port_mapping") and dff_gate.port_mapping:
            for port, net_name in dff_gate.port_mapping.items():
                if port.lower() == "d":
                    return net_name

        # fallback: choose first non-clock/reset input
        for n in getattr(dff_gate, "input_nets", []):
            lname = n.name.lower()
            if (
                "clk" in lname
                or lname in ["ck", "rn", "sn", "rst", "reset", "set"]
            ):
                continue
            return n.name

        return None

    def format_op(g_type, ins):
        if g_type == "not":
            if len(ins) < 1:
                return "UNKNOWN_NOT"
            return f"(~{ins[0]})"

        if g_type == "buf":
            if len(ins) < 1:
                return "UNKNOWN_BUF"
            return ins[0]

        if g_type == "and":
            return f"({' & '.join(ins)})"

        if g_type == "or":
            return f"({' | '.join(ins)})"

        if g_type == "nand":
            return f"(~({' & '.join(ins)}))"

        if g_type == "nor":
            return f"(~({' | '.join(ins)}))"

        if g_type == "xor":
            return f"({' ^ '.join(ins)})"

        if g_type == "xnor":
            return f"(~({' ^ '.join(ins)}))"

        return None

    def shorten_expr(expr: str, limit: int = 1800) -> str:
        """
        Prevent massive output while keeping enough information.
        """
        if len(expr) <= limit:
            return expr

        head = expr[:limit // 2]
        tail = expr[-limit // 2:]

        return (
            head
            + "\n    ... [expression truncated for readability] ...\n    "
            + tail
        )

    # ------------------------------------------------------------
    # Recursive expression builder
    # ------------------------------------------------------------
    memo = {}
    visiting = set()
    boundary_notes = []
    state_vars = set()
    undriven_vars = set()
    truncated = False

    def build_expr(net_name, depth=0):
        nonlocal truncated

        net_name = str(net_name).strip()

        if net_name in memo:
            return memo[net_name]

        if depth > 80:
            expr = f"{net_name}_TRUNCATED"
            truncated = True
            boundary_notes.append(
                f"Traversal depth exceeded at '{net_name}', expression was truncated."
            )
            memo[net_name] = expr
            return expr

        if net_name in visiting:
            expr = f"{net_name}_CYCLE"
            boundary_notes.append(
                f"Combinational cycle or repeated traversal detected at '{net_name}'."
            )
            memo[net_name] = expr
            return expr

        # Constants
        if is_const_net(net_name):
            memo[net_name] = net_name
            return net_name

        # Primary input, including bus bit
        if is_primary_input_name(net_name):
            memo[net_name] = net_name
            return net_name

        net = current_design.nets.get(net_name)

        if net is None:
            expr = net_name
            undriven_vars.add(net_name)
            boundary_notes.append(
                f"Net '{net_name}' was not found in the design graph; treated as symbolic input."
            )
            memo[net_name] = expr
            return expr

        if not net.driver:
            expr = net_name
            undriven_vars.add(net_name)
            boundary_notes.append(
                f"Net '{net_name}' has no driver; treated as symbolic input or undriven boundary."
            )
            memo[net_name] = expr
            return expr

        driver = net.driver

        # INPUT_PORT marker
        if isinstance(driver, str):
            memo[net_name] = net_name
            return net_name

        g_type = driver.gate_type.strip().lower()

        # DFF Q output: state boundary
        if g_type == "dff":
            expr = net_name
            state_vars.add(net_name)
            boundary_notes.append(
                f"Net '{net_name}' is driven by DFF '{driver.inst_name}' as a state element output; "
                f"it is treated as a state variable."
            )
            memo[net_name] = expr
            return expr

        visiting.add(net_name)

        ins = []
        for n in driver.input_nets:
            ins.append(build_expr(n.name, depth + 1))

        visiting.remove(net_name)

        expr = format_op(g_type, ins)

        if expr is None:
            expr = net_name
            boundary_notes.append(
                f"Unsupported gate type '{driver.gate_type}' at instance '{driver.inst_name}', "
                f"treated '{net_name}' as symbolic boundary."
            )

        memo[net_name] = expr
        return expr

    # ------------------------------------------------------------
    # Main target handling
    # ------------------------------------------------------------
    target_obj = current_design.nets.get(target_net)

    if target_obj is None:
        return (
            f"Error: Net '{target_net}' not found in the current design. "
            f"Cannot derive Boolean equation."
        )

    # Case 1: target itself is DFF Q
    if (
        target_obj.driver
        and not isinstance(target_obj.driver, str)
        and target_obj.driver.gate_type.strip().lower() == "dff"
    ):
        dff_gate = target_obj.driver
        d_net_name = get_dff_d_net_name(dff_gate)

        if d_net_name:
            next_expr = build_expr(d_net_name)
            shown_expr = shorten_expr(next_expr)

            if state_vars:
                scope_line = (
                    "The next-state equation is expressed in terms of primary inputs "
                    "and current-state variables, because the fan-in cone crosses DFF Q boundaries."
                )
            else:
                scope_line = (
                    "The next-state equation is expressed purely in terms of primary inputs "
                    "and constants."
                )

            msg = (
                f"Boolean equation for {target_obj.name}:\n"
                f"- Current-state equation: {target_obj.name} = {target_obj.name} "
                f"(registered output driven by DFF '{dff_gate.inst_name}')\n"
                f"- Scope: {scope_line}\n"
                f"- Next-state equation:\n"
                f"  {target_obj.name}_next = {shown_expr}"
            )
        else:
            msg = (
                f"Boolean equation for {target_obj.name}:\n"
                f"- Current-state equation: {target_obj.name} = {target_obj.name} "
                f"(registered output driven by DFF '{dff_gate.inst_name}')\n"
                f"- Next-state equation: unavailable because D input could not be identified."
            )

        if state_vars:
            preview = ", ".join(sorted(state_vars)[:20])
            extra = "" if len(state_vars) <= 20 else f", ... ({len(state_vars)} total)"
            msg += f"\nState variables encountered: {preview}{extra}"

        if undriven_vars:
            preview = ", ".join(sorted(undriven_vars)[:10])
            extra = "" if len(undriven_vars) <= 10 else f", ... ({len(undriven_vars)} total)"
            msg += f"\nUndriven/symbolic boundaries encountered: {preview}{extra}"

        if truncated:
            msg += "\nNote: The expression was truncated for readability."

        if boundary_notes:
            unique_notes = []
            for note in boundary_notes:
                if note not in unique_notes:
                    unique_notes.append(note)
            msg += "\nNotes:\n" + "\n".join(f"- {n}" for n in unique_notes[:10])

            return msg

    # Case 2: pure combinational target or symbolic boundary
    expr = build_expr(target_obj.name)
    shown_expr = shorten_expr(expr)

    if state_vars:
        scope_line = (
            "Expression is in terms of primary inputs and current-state variables, "
            "because traversal crosses DFF Q boundaries."
        )
    else:
        scope_line = "Expression is in terms of primary inputs and constants."

    msg = (
        f"Boolean equation for {target_obj.name}:\n"
        f"- Scope: {scope_line}\n"
        f"- Equation:\n"
        f"  {target_obj.name} = {shown_expr}"
    )

    if expr == target_obj.name:
        if is_primary_input_name(target_obj.name):
            msg += (
                f"\nNote: '{target_obj.name}' is a primary input, so its Boolean equation "
                f"in terms of primary inputs is itself."
            )
        elif not target_obj.driver:
            msg += (
                f"\nNote: '{target_obj.name}' has no driver in the current design graph, "
                f"so it was treated as a symbolic boundary."
            )
        else:
            msg += (
                f"\nNote: '{target_obj.name}' resolved to itself because traversal stopped "
                f"at a sequential or unsupported boundary."
            )

    if state_vars:
        preview = ", ".join(sorted(state_vars)[:20])
        extra = "" if len(state_vars) <= 20 else f", ... ({len(state_vars)} total)"
        msg += f"\nState variables encountered: {preview}{extra}"

    if undriven_vars:
        preview = ", ".join(sorted(undriven_vars)[:10])
        extra = "" if len(undriven_vars) <= 10 else f", ... ({len(undriven_vars)} total)"
        msg += f"\nUndriven/symbolic boundaries encountered: {preview}{extra}"

    if truncated:
        msg += "\nNote: The expression was truncated for readability."

    if boundary_notes:
        unique_notes = []
        for note in boundary_notes:
            if note not in unique_notes:
                unique_notes.append(note)
        msg += "\nNotes:\n" + "\n".join(f"- {n}" for n in unique_notes[:10])

    return msg


def insert_dedicated_buffers(target_net: str) -> str:
    """Insert a BUF gate on a signal so that EACH load is driven through its own dedicated buffer (Star-Routing)."""
    global current_design, current_analyzer, cpp_graph, last_added_buffer_count # 👈 加入全域變數
    log_output(f"> [System 執行工具]: insert_dedicated_buffers('{target_net}')")
    if current_design is None: return "Error: No design loaded."
    
    try:
        net = current_design.nets.get(target_net)
        if not net: return f"Error: Net {target_net} not found."
        
        added = 0
        original_loads = list(net.loads)
        net.loads = [ld for ld in original_loads if ld == 'OUTPUT_PORT']
        
        from parser import Gate, LogicAnalyzer
        import fast_eda
        
        for load in original_loads:
            if load == 'OUTPUT_PORT': continue
            
            buf_inst = f"dedic_buf_{target_net}_{added}"
            buf_net_name = f"net_{buf_inst}"
            
            buf_gate = Gate(buf_inst, "buf")
            buf_net = current_design.get_or_create_net(buf_net_name)
            
            buf_gate.input_nets = [net]
            buf_gate.output_nets = [buf_net]
            buf_net.driver = buf_gate
            buf_net.loads.append(load)
            
            net.loads.append(buf_gate)
            current_design.gates[buf_inst] = buf_gate
            
            for i in range(len(load.input_nets)):
                if load.input_nets[i].name == target_net:
                    load.input_nets[i] = buf_net
            if hasattr(load, 'port_mapping') and load.port_mapping:
                for port, n_name in load.port_mapping.items():
                    if n_name == target_net:
                        load.port_mapping[port] = buf_net_name
            added += 1
            
        current_analyzer = LogicAnalyzer(current_design)
        cpp_graph = fast_eda.FastGraph()
        for gate_name, gate_obj in current_design.gates.items():
            in_ns = [n.name for n in gate_obj.input_nets]
            for out_n in gate_obj.output_nets:
                cpp_graph.add_net_driver(out_n.name, gate_name, in_ns)
                
        # 🌟 關鍵：將數量存入全域變數！
        last_added_buffer_count = added
        
        msg = f"Success! Inserted {added} dedicated BUF gates for each load of {target_net}."
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
    except Exception as e:
        return f"Error: {str(e)}"

def report_last_added_buffer_count() -> str:
    """Report how many BUF gates were added."""
    global last_added_buffer_count
    log_output("> [System 執行工具]: report_last_added_buffer_count()")
    
    # 將結構變得更嚴格，減少它誤判的可能性
    ans = f"Total BUF gates added: {last_added_buffer_count}."
    log_output(f"  -> [工具回傳]: {ans}")
    
    return f"You must output this exact string: {ans}"

def verify_internal_signals(net1: str, net2: str = "") -> str:
    """
    Use Yosys SAT solver to formally prove properties of internal signals.
    - If net2 is empty: Proves if net1 is ALWAYS 0 regardless of all inputs.
    - If net2 is provided: Proves if net1 and net2 are functionally equivalent.
    """
    global current_design
    log_output(f"> [System 執行工具]: verify_internal_signals('{net1}', '{net2}')")
    if current_design is None: return "Error: No design loaded."
    
    try:
        import os, subprocess, tempfile
        from parser import Gate
        
        n1 = current_design.nets.get(net1)
        if not n1: return f"Error: Net {net1} not found."
        
        # 動態掛載測試點 (Test Point)
        dummy_gate = Gate("yosys_dummy_probe", "xor" if net2 else "buf")
        dummy_out = current_design.get_or_create_net("yosys_sat_target")
        
        dummy_gate.input_nets.append(n1)
        n1.loads.append(dummy_gate)
        
        if net2:
            n2 = current_design.nets.get(net2)
            if not n2: return f"Error: Net {net2} not found."
            dummy_gate.input_nets.append(n2)
            n2.loads.append(dummy_gate)
            
        dummy_gate.output_nets.append(dummy_out)
        dummy_out.driver = dummy_gate
        current_design.gates["yosys_dummy_probe"] = dummy_gate
        current_design.outputs.append("yosys_sat_target")
        
        # 導出包含了測試點的 Verilog
        verilog_code = current_design.to_verilog()
        
        # 🌟 立即拔除測試點 (神不知鬼不覺)
        current_design.outputs.remove("yosys_sat_target")
        del current_design.gates["yosys_dummy_probe"]
        n1.loads.remove(dummy_gate)
        if net2: n2.loads.remove(dummy_gate)
        del current_design.nets["yosys_sat_target"]
        
        # 呼叫 Yosys 進行硬核 SAT 求解
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.v') as tmp:
            tmp.write(verilog_code)
            tmp_path = tmp.name
            
        yosys_cmd = f"read_verilog {tmp_path}; prep -top top; sat -verify -prove yosys_sat_target 0"
        result = subprocess.run(["yosys", "-p", yosys_cmd], capture_output=True, text=True)
        os.remove(tmp_path)
        
        is_proven = "Successfully proved" in result.stdout or "Proof successful" in result.stdout or "proved" in result.stdout.lower()
        
        if net2:
            msg = f"Result: '{net1}' and '{net2}' are {'functionally equivalent' if is_proven else 'NOT equivalent'}."
        else:
            msg = f"Result: '{net1}' is {'ALWAYS 0' if is_proven else 'NOT always 0'} regardless of inputs."
            
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        return f"Error running Yosys SAT: {str(e)}"
    
def optimize_fanout(max_loads: int) -> str:
    """
    Insert buffers to limit the maximum fanout (loads) of any net in the current design.
    - max_loads: The maximum number of gates a single net is allowed to drive.
    """
    global current_design
    log_output(f"> [System 執行工具]: optimize_fanout(max_loads={max_loads})")
    
    if current_design is None:
        err_msg = "Error: No design loaded."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        # 🚀 關鍵修正：改為呼叫我們剛修好的 current_design.optimize_fanout (而不是舊的 fix_fanout)
        # 並且直接接住它回傳的正確字串，這樣常數防呆的結果才會真實反映在 Log 中！
        msg = current_design.optimize_fanout(max_loads)
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        err_msg = f"Error optimizing fanout: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def restructure_design() -> str:
    """
    Reduce the critical path depth of the current design through aggressive logic restructuring.
    Call this tool when the user wants to minimize the critical path or reduce logic levels aggressively.
    """
    global current_design, current_analyzer, cpp_graph
    log_output("> [System 執行工具]: restructure_design() -> 呼叫 Berkeley ABC")
    
    if current_design is None:
        return "Error: No design loaded."
        
    try:
        import re
        import os
        import subprocess
        from parser import Gate
        
        original_gates = current_design.gates.copy()
        
        # 🌟 1. 建立無敵隔離沙盒
        dff_gates = {}
        comb_gates = {}
        
        true_pi = set(current_design.inputs)
        true_po = set(current_design.outputs)
        
        sandbox_inputs = set()
        sandbox_outputs = set()
        all_comb_nets = set()

        for inst, gate in current_design.gates.items():
            # 🌟 絕對防禦 1：無畏大小寫與空白，精準提取 DFF！
            if gate.gate_type.strip().lower() == 'dff':
                dff_gates[inst] = gate
                
                # 🛡️ 雙重備援：確保 DFF 的腳位被完美保留
                if gate.port_mapping:
                    for port, net_name in gate.port_mapping.items():
                        if port.lower() in ['q', 'qn']:
                            sandbox_inputs.add(net_name)
                        else:
                            sandbox_outputs.add(net_name)
                else:
                    for n in gate.output_nets:
                        sandbox_inputs.add(n.name)
                    for n in gate.input_nets:
                        sandbox_outputs.add(n.name)
            else:
                comb_gates[inst] = gate
                for n in gate.input_nets:
                    all_comb_nets.add(n.name)
                    clean_name = n.name.replace('\\', '').strip()
                    base = clean_name.split('[')[0]
                    if base in true_pi or clean_name in true_pi or n.name in true_pi:
                        sandbox_inputs.add(n.name)
                for n in gate.output_nets:
                    all_comb_nets.add(n.name)
                    clean_name = n.name.replace('\\', '').strip()
                    base = clean_name.split('[')[0]
                    if base in true_po or clean_name in true_po or n.name in true_po:
                        sandbox_outputs.add(n.name)

        for po in current_design.outputs:
            if po in all_comb_nets or po in sandbox_inputs:
                sandbox_outputs.add(po)

        constants = {n for n in all_comb_nets if "'" in n or n.isdigit()}
        internal_nets = all_comb_nets - sandbox_inputs - sandbox_outputs - constants

        # 🌟 2. 絕對安全的變數映射 (洗掉所有中括號)
        net_map = {}
        rev_net_map = {}
        def get_safe(orig):
            if orig not in net_map:
                safe = f"s_net_{len(net_map)}"
                net_map[orig] = safe
                rev_net_map[safe] = orig
            return net_map[orig]

        safe_inputs = []
        safe_outputs = []
        safe_internals = []
        proxy_bufs = []

        for n in sandbox_inputs:
            if n not in constants:
                safe_inputs.append(get_safe(n))

        for n in sandbox_outputs:
            if n in constants: continue
            if n in sandbox_inputs:
                s_out = f"s_outproxy_{len(proxy_bufs)}"
                safe_outputs.append(s_out)
                proxy_bufs.append(f"  buf b_outproxy_{len(proxy_bufs)} ({s_out}, {get_safe(n)});")
                rev_net_map[s_out] = n
            else:
                safe_outputs.append(get_safe(n))

        for n in internal_nets:
            safe_internals.append(get_safe(n))

        # 🌟 3. 打造 ABC 100% 聽話的 Verilog 字串 (加上絕對防禦多重驅動過濾)
        v_lines = []
        all_ports = list(set(safe_inputs + safe_outputs))
        if not all_ports:
            all_ports = ["dummy_port"]
            safe_inputs.append("dummy_port")
            
        port_chunks = [", ".join(all_ports[i:i+50]) for i in range(0, len(all_ports), 50)]
        v_lines.append("module top (\n  " + ",\n  ".join(port_chunks) + "\n);")
        
        for i in range(0, len(safe_inputs), 50):
            v_lines.append(f"  input {', '.join(safe_inputs[i:i+50])};")
        for i in range(0, len(safe_outputs), 50):
            v_lines.append(f"  output {', '.join(safe_outputs[i:i+50])};")
        if safe_internals:
            for i in range(0, len(safe_internals), 50):
                v_lines.append(f"  wire {', '.join(safe_internals[i:i+50])};")
        
        # 🚀 終極防護網：記錄已經被驅動過的線，防止 ABC 斷言崩潰
        driven_nets = set(safe_inputs)
        
        for c in constants:
            v_lines.append(f"  wire {get_safe(c)};")
            buf_out = get_safe(c)
            if buf_out not in driven_nets:
                v_lines.append(f"  buf b_const_{len(proxy_bufs)}_{buf_out} ({buf_out}, {c});")
                driven_nets.add(buf_out)

        for inst, gate in comb_gates.items():
            s_ins = [get_safe(n.name) for n in gate.input_nets]
            
            # 🛑 防呆 1：如果這顆邏輯閘沒有輸入線 (斷線孤兒)，直接拋棄，否則 ABC 會報錯！
            if not s_ins:
                continue

            actual_outs = []
            for n in gate.output_nets:
                safe_out = get_safe(n.name)
                # 🛑 防呆 2：若發現這條線已經被推動過了，自動產生替身防止崩潰！
                if safe_out in driven_nets:
                    safe_out = f"{safe_out}_dup_{inst}"
                    v_lines.append(f"  wire {safe_out};") # 👈 關鍵：必須明確宣告這條新的替身線！
                driven_nets.add(safe_out)
                actual_outs.append(safe_out)
                
            if not actual_outs: 
                continue
            
            ports = ", ".join(actual_outs + s_ins)
            g_type = gate.gate_type.strip().lower() # 👈 關鍵：強制轉小寫，防止大寫 AND 搞瞎 ABC
            v_lines.append(f"  {g_type} abc_orig_{inst} ({ports});")
            
        for b in proxy_bufs:
            v_lines.append(b)
            
        v_lines.append("endmodule")
        
        temp_in = "temp_abc_in.v"
        temp_out = "temp_abc_out.v"
        with open(temp_in, "w", encoding="utf-8") as f:
            f.write("\n".join(v_lines))

        # 🌟 4. 呼叫 ABC 引擎執行極限深度優化
        current_dir = os.path.dirname(os.path.abspath(__file__))
        abc_path = os.path.join(current_dir, "third_party", "abc", "abc")
        
        abc_cmd = f"read_verilog {temp_in}; strash; balance; rewrite; write_verilog {temp_out}"
        result = subprocess.run([abc_path, "-c", abc_cmd], capture_output=True, text=True)
        
        if "failed" in result.stdout.lower() or "assertion" in result.stderr.lower():
            raise Exception(f"ABC Engine Error:\n{result.stdout}\n{result.stderr}")
        if not os.path.exists(temp_out) or os.path.getsize(temp_out) == 0:
            raise Exception("ABC failed to generate output!")
            
        # 🌟 5. 自製微型解析器：無痛翻譯回 Python 物件
        with open(temp_out, "r", encoding="utf-8") as f:
            abc_code = f.read()
            
        abc_code = re.sub(r'\\([a-zA-Z0-9_]+(?:\[\d+\])?)\s', r'\1', abc_code) 
        lines = abc_code.split('\n')
        merged_lines = []
        curr = ""
        for line in lines:
            line = re.sub(r'//.*', '', line).strip()
            if not line: continue
            curr = (curr + " " + line) if curr else line
            if curr.endswith(';') or curr.endswith('endmodule'):
                merged_lines.append(curr)
                curr = ""
                
        new_comb_gates = {}
        global_gate_counter = 0

        # 🚀 關鍵修復 2：無畏大小寫與空白，安全清理 Net 負載！
        for net in current_design.nets.values():
            net.loads = [ld for ld in net.loads if ld == 'OUTPUT_PORT' or getattr(ld, 'gate_type', '').strip().lower() == 'dff']
            if getattr(net.driver, 'gate_type', '').strip().lower() != 'dff':
                net.driver = None

        def add_gate(g_type, out_n, in_ns):
            nonlocal global_gate_counter
            real_out = rev_net_map.get(out_n, out_n)
            real_ins = [rev_net_map.get(in_n, in_n) for in_n in in_ns]
            
            if g_type == 'buf' and len(real_ins) == 1 and real_out == real_ins[0]:
                return
                
            g_inst = f"abc_g_{global_gate_counter}"
            global_gate_counter += 1
            g = Gate(g_inst, g_type)
            
            out_net_obj = current_design.get_or_create_net(real_out)
            g.output_nets = [out_net_obj]
            out_net_obj.driver = g
            
            for real_in in real_ins:
                in_net_obj = current_design.get_or_create_net(real_in)
                g.input_nets.append(in_net_obj)
                if g not in in_net_obj.loads:
                    in_net_obj.loads.append(g)
                    
            new_comb_gates[g_inst] = g

        for line in merged_lines:
            if line.startswith('module ') or line.startswith('input ') or line.startswith('output ') or line.startswith('wire ') or line == 'endmodule':
                continue
                
            m_gate = re.match(r'^\s*(and|nand|or|nor|xor|xnor|not|buf)\s+([a-zA-Z0-9_]+\s+)?\((.*?)\)\s*;', line)
            if m_gate:
                g_type = m_gate.group(1)
                ports = [p.strip() for p in m_gate.group(3).split(',')]
                add_gate(g_type, ports[0], ports[1:])
                continue
                
            m_assign = re.match(r'^\s*assign\s+([a-zA-Z0-9_\[\]\\]+)\s*=\s*(.+?)\s*;', line)
            if m_assign:
                lhs = m_assign.group(1).replace('\\', '').strip()
                rhs = m_assign.group(2).strip()

                
                is_inv = False
                if rhs.startswith('~(') and rhs.endswith(')'):
                    is_inv = True
                    rhs = rhs[2:-1].strip()
                    
                if '&' not in rhs and '|' not in rhs and '^' not in rhs:
                    if rhs.startswith('~'): add_gate('not', lhs, [rhs[1:].strip()])
                    else: add_gate('buf', lhs, [rhs])
                    continue
                    
                for op_char, base_g in [('&', 'and'), ('|', 'or'), ('^', 'xor')]:
                    if op_char in rhs:
                        parts = [p.strip() for p in rhs.split(op_char)]
                        if len(parts) >= 2:
                            op1, op2 = parts[0], parts[1]
                            if op1.startswith('~'):
                                inv_op1 = f"abc_inv_{global_gate_counter}_1"
                                add_gate('not', inv_op1, [op1[1:].strip()])
                                op1 = inv_op1
                            if op2.startswith('~'):
                                inv_op2 = f"abc_inv_{global_gate_counter}_2"
                                add_gate('not', inv_op2, [op2[1:].strip()])
                                op2 = inv_op2
                                
                            final_g = base_g
                            if is_inv:
                                if base_g == 'and': final_g = 'nand'
                                elif base_g == 'or': final_g = 'nor'
                                elif base_g == 'xor': final_g = 'xnor'
                                
                            add_gate(final_g, lhs, [op1, op2])
                        break

        # 🌟 6. 完美縫合 (還原 DFF 與 Graph)
        current_design.gates = {**dff_gates, **new_comb_gates}
        
        from parser import LogicAnalyzer
        import fast_eda
        global current_analyzer, cpp_graph
        current_analyzer = LogicAnalyzer(current_design)
        cpp_graph = fast_eda.FastGraph()
        for gate_name, gate_obj in current_design.gates.items():
            in_ns = [n.name for n in gate_obj.input_nets]
            for out_n in gate_obj.output_nets:
                cpp_graph.add_net_driver(out_n.name, gate_name, in_ns)
                
        msg = "Success! Logic restructured perfectly via pure sandbox."
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        import traceback
        err_msg = f"Error running ABC: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        if 'original_gates' in locals():
            current_design.gates = original_gates
        return err_msg
def map_to_gate_set(target_gates_str: str, cone_net: str = "") -> str:
    """
    動態技術映射核心引擎 (賽事決戰最終完美強固版)。
    
    - 徹底修復 32 unproven cells: 全面攔截並轉換 assign 為實體 buf。
    - 保留 DFF 的 Clock/Reset 邏輯錐。
    - 防止 abc_safe_top 的 I/O 宣告污染。
    - [NEW] 支援局部重組：若提供 cone_net，則僅針對該 net 的 Fan-in cone 進行重組。
    """

    global current_design, _sync_cpp_engine, current_design_filepath

    if current_design is None:
        return "Error: No design loaded."

    import os
    import re
    import subprocess
    import traceback
    import tempfile

    # 🌟 [NEW] 如果沒讀過檔案 (例如從 scratch 建立)，則先導出一個暫存 Verilog 給後面的 stitch 使用
    if not current_design_filepath:
        try:
            with tempfile.NamedTemporaryFile(suffix=".v", delete=False, mode="w") as f:
                current_design_filepath = f.name
                f.write(current_design.to_verilog())
        except Exception as e:
            return f"Error: current_design_filepath is empty and failed to create temp file: {str(e)}"

    if not target_gates_str:
        return "Error: 'target_net' must contain allowed gate types, e.g., 'AND, NOT' or 'NAND'."

    # ============================================================
    # 0. Localized Cone Detection
    # ============================================================
    target_gates_only = set()
    if cone_net:
        log_output(f"  -> [局部重組模式]: Target net = {cone_net}")
        
        # 🌟 智慧探針：如果 target_net 是被 DFF 推動的，我們應該重組 DFF 的 D 端輸入邏輯錐
        search_start_nets = [cone_net]
        n_obj = current_design.nets.get(cone_net)
        if n_obj and n_obj.driver and not isinstance(n_obj.driver, str):
            if n_obj.driver.gate_type.lower() == 'dff':
                dff_gate = n_obj.driver
                log_output(f"  -> [探針發現]: {cone_net} 由 DFF {dff_gate.inst_name} 推動，自動轉向重組其 D 端邏輯。")
                if hasattr(dff_gate, "port_mapping") and dff_gate.port_mapping:
                    d_net = dff_gate.port_mapping.get('D') or dff_gate.port_mapping.get('d')
                    if d_net: search_start_nets = [str(d_net)]
                else:
                    # 嘗試從 input_nets 找非 clock/reset 的線
                    for in_n in dff_gate.input_nets:
                        if 'clk' not in in_n.name.lower() and 'rst' not in in_n.name.lower():
                            search_start_nets = [in_n.name]
                            break

        visited_gates = set()
        queue = search_start_nets
        while queue:
            curr = queue.pop(0)
            n_obj = current_design.nets.get(curr)
            if not n_obj or not n_obj.driver or type(n_obj.driver) == str: continue
            gate = n_obj.driver
            # 我們只把組合邏輯加入 target_gates_only，因為 ABC map 是組合邏輯技術映射
            if gate.gate_type.strip().lower() == 'dff': continue
            
            if gate.inst_name not in visited_gates:
                visited_gates.add(gate.inst_name)
                for in_n in gate.input_nets:
                    queue.append(in_n.name)
        
        target_gates_only = visited_gates
        if not target_gates_only:
            return f"Error: No combinational gates found in the fan-in cone of '{cone_net}'."

    # ============================================================
    # 1. Parse allowed gate set
    # ============================================================
    allowed_gates = [
        g.strip().upper()
        for g in target_gates_str.replace("&amp;", ",").replace("&", ",").split(",")
        if g.strip()
    ]
    allowed_set = set(allowed_gates)
    valid_gates = {"AND", "OR", "NOT", "NAND", "NOR", "XOR", "XNOR", "BUF"}

    unknown = allowed_set - valid_gates
    if unknown:
        return f"Error: Unsupported gate type(s): {', '.join(sorted(unknown))}"

    if not allowed_set:
        return f"Error: No valid primitive gate types found in target '{target_gates_str}'."

    functionally_complete = False
    if "NAND" in allowed_set or "NOR" in allowed_set:
        functionally_complete = True
    if "NOT" in allowed_set and ("AND" in allowed_set or "OR" in allowed_set):
        functionally_complete = True

    if not functionally_complete:
        return (
            "Error: target gate set is not functionally complete for general mapping. "
            f"Received: {', '.join(allowed_gates)}. "
            "Use examples like 'AND, NOT', 'OR, NOT', 'NAND', or 'NOR'."
        )

    # ============================================================
    # 2. ABC binary path
    # ============================================================
    current_dir = os.path.dirname(os.path.abspath(__file__))
    abc_path = os.path.join(current_dir, "third_party", "abc", "abc")

    if not os.path.exists(abc_path):
        return f"Error: ABC executable not found at '{abc_path}'."
    if not os.access(abc_path, os.X_OK):
        return f"Error: ABC is not executable: '{abc_path}'."

    # ============================================================
    # 3. Snapshot original design & Paths
    # ============================================================
    original_inputs = list(getattr(current_design, "inputs", []))
    original_outputs = list(getattr(current_design, "outputs", []))
    original_gates = getattr(current_design, "gates", {}).copy()

    genlib_path = "temp.genlib"
    temp_in = "temp_map_in.v"
    temp_out = "temp_map_out.v"
    temp_final = "temp_map_final.v"

    def safe_remove(path):
        try:
            if os.path.exists(path): os.remove(path)
        except Exception: pass

    def is_const_name(n):
        n = str(n).strip().lower()
        return "'" in n or n in {"0", "1", "1'b0", "1'b1", "1'h0", "1'h1", "1'd0", "1'd1"}

    def normalize_const(n):
        n = str(n).strip().lower()
        if n in {"0", "1'b0", "1'h0", "1'd0"}: return "1'b0"
        if n in {"1", "1'b1", "1'h1", "1'd1"}: return "1'b1"
        return str(n)

    def net_obj_name(n):
        return n.name if hasattr(n, "name") else str(n)

    def verilog_id(name):
        name = str(name).strip()
        if is_const_name(name): return normalize_const(name)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*(\[.*?\])*$", name):
            return name
        return "\\" + name.replace("\\", "") + " "


    # ============================================================
    # 4. Build dynamic genlib
    # ============================================================
    def build_genlib():
        genlib_lines = [
            "GATE _ZERO_ 0 O=CONST0;",  
            "GATE _ONE_  0 O=CONST1;",  
            "GATE _NOT_ 1 O=!a; PIN * INV 1 999 1 0 1 0",
            "GATE _BUF_ 1 O=a;  PIN * NONINV 1 999 1 0 1 0"
        ]
        if "AND" in allowed_set:  genlib_lines.append("GATE _AND_  1 O=(a*b);  PIN * NONINV 1 999 1 0 1 0")
        if "OR" in allowed_set:   genlib_lines.append("GATE _OR_   1 O=(a+b);  PIN * NONINV 1 999 1 0 1 0")
        if "NAND" in allowed_set: genlib_lines.append("GATE _NAND_ 1 O=!(a*b); PIN * INV    1 999 1 0 1 0")
        if "NOR" in allowed_set:  genlib_lines.append("GATE _NOR_  1 O=!(a+b); PIN * INV    1 999 1 0 1 0")
        if "XOR" in allowed_set:  genlib_lines.append("GATE _XOR_  2 O=(a^b);  PIN * NONINV 1 999 1 0 1 0")
        if "XNOR" in allowed_set: genlib_lines.append("GATE _XNOR_ 2 O=!(a^b); PIN * INV    1 999 1 0 1 0")
        return "\n".join(genlib_lines) + "\n"

    # ============================================================
    # 5. Build ABC-safe sandbox
    # ============================================================
    def build_abc_safe_verilog():
        net_map, rev_net_map = {}, {}

        def safe_net(orig):
            orig = str(orig).strip()
            if is_const_name(orig): return normalize_const(orig)
            if orig not in net_map:
                s = f"n_{len(net_map)}"
                net_map[orig] = s
                rev_net_map[s] = orig
            return net_map[orig]

        def get_base_name(name):
            name_clean = str(name).strip()
            m = re.match(r'^(.+?)\[\d+\]$', name_clean)
            if m:
                base = m.group(1).strip()
                if base.startswith('\\'):
                    return base + ' '
                return base
            if name_clean.startswith('\\'):
                return name_clean + ' '
            return name_clean

        def clean_true_name(name):
            name = str(name).strip()
            if name.startswith('\\'):
                return name + ' '
            return name

        def is_vector(name):
            name_clean = str(name).strip()
            if hasattr(current_design, "net_widths") and name_clean in current_design.net_widths:
                return True
            prefix = name_clean + "["
            if any(net_name.startswith(prefix) for net_name in current_design.nets):
                return True
            return False

        true_inputs = {clean_true_name(n) for n in original_inputs}
        true_outputs = {clean_true_name(n) for n in original_outputs}
        sandbox_inputs, sandbox_outputs, all_comb_nets = set(), set(), set()
        comb_gates = []

        # 🌟 [NEW] 局部重組過濾邏輯
        if target_gates_only:
            for inst, gate in original_gates.items():
                if inst in target_gates_only:
                    comb_gates.append((inst, gate))
                    for n in gate.input_nets:
                        nn = net_obj_name(n)
                        if not is_const_name(nn):
                            all_comb_nets.add(nn)
                    for n in gate.output_nets:
                        nn = net_obj_name(n)
                        if not is_const_name(nn):
                            all_comb_nets.add(nn)
            
            # 重新計算 I/O
            driven_in_cone = set()
            for inst, gate in comb_gates:
                for n in gate.output_nets:
                    driven_in_cone.add(net_obj_name(n))
            
            for inst, gate in comb_gates:
                for n in gate.input_nets:
                    nn = net_obj_name(n)
                    if not is_const_name(nn) and nn not in driven_in_cone:
                        sandbox_inputs.add(nn)
                for n in gate.output_nets:
                    nn = net_obj_name(n)
                    if not is_const_name(nn):
                        sandbox_outputs.add(nn)
        else:
            for inst, gate in original_gates.items():
                gtype = str(getattr(gate, "gate_type", "")).strip().lower()
                input_nets = getattr(gate, "input_nets", [])
                output_nets = getattr(gate, "output_nets", [])

                if gtype == "dff":
                    port_mapping = getattr(gate, "port_mapping", None)
                    if port_mapping:
                        for port, net_name in port_mapping.items():
                            p, nn = str(port).lower(), str(net_name)
                            if not is_const_name(nn):
                                all_comb_nets.add(nn)
                                if p in {"q", "qn", "q_n"}:
                                    sandbox_inputs.add(nn)
                                    if get_base_name(nn) in true_outputs:
                                        sandbox_outputs.add(nn)
                                else:
                                    sandbox_outputs.add(nn)
                                    if get_base_name(nn) in true_inputs:
                                        sandbox_inputs.add(nn)
                    else:
                        for n in output_nets:
                            nn = net_obj_name(n)
                            if not is_const_name(nn):
                                all_comb_nets.add(nn)
                                sandbox_inputs.add(nn)
                                if get_base_name(nn) in true_outputs:
                                    sandbox_outputs.add(nn)
                        for n in input_nets:
                            nn = net_obj_name(n)
                            if not is_const_name(nn):
                                all_comb_nets.add(nn)
                                sandbox_outputs.add(nn)
                                if get_base_name(nn) in true_inputs:
                                    sandbox_inputs.add(nn)
                    continue

                comb_gates.append((inst, gate))
                for n in input_nets:
                    nn = net_obj_name(n)
                    if not is_const_name(nn):
                        all_comb_nets.add(nn)
                        if get_base_name(nn) in true_inputs:
                            sandbox_inputs.add(nn)
                for n in output_nets:
                    nn = net_obj_name(n)
                    if not is_const_name(nn):
                        all_comb_nets.add(nn)
                        if get_base_name(nn) in true_outputs:
                            sandbox_outputs.add(nn)

        if not target_gates_only:
            for n in true_inputs:
                cleaned = clean_true_name(n)
                if not is_const_name(cleaned) and not is_vector(cleaned):
                    sandbox_inputs.add(cleaned)
                    all_comb_nets.add(cleaned)
            for n in true_outputs:
                cleaned = clean_true_name(n)
                if not is_const_name(cleaned) and not is_vector(cleaned):
                    sandbox_outputs.add(cleaned)
                    all_comb_nets.add(cleaned)

        output_proxy = {}
        for po in list(sandbox_outputs):
            if po in sandbox_inputs:
                proxy = f"{po}__outproxy".replace('[', '_').replace(']', '_').replace(' ', '_').replace('\\', '_')
                output_proxy[po] = proxy
                all_comb_nets.add(proxy)

        safe_inputs = [safe_net(n) for n in sorted(sandbox_inputs) if not is_const_name(n)]
        safe_outputs = [safe_net(output_proxy.get(n, n)) for n in sorted(sandbox_outputs) if not is_const_name(n)]
        
        safe_internal_set = set()
        for n in all_comb_nets:
            if is_const_name(n): continue
            s = safe_net(n)
            if s not in safe_inputs and s not in safe_outputs: safe_internal_set.add(s)
        for orig, proxy in output_proxy.items():
            sp = safe_net(proxy)
            if sp not in safe_inputs and sp not in safe_outputs: safe_internal_set.add(sp)

        lines = []
        ports = safe_inputs + safe_outputs
        if ports:
            lines.append("module abc_safe_top(\n  " + ", ".join(ports) + "\n);")
        else:
            lines.append("module abc_safe_top();")

        if safe_inputs: lines.append("  input " + ", ".join(safe_inputs) + ";")
        if safe_outputs: lines.append("  output " + ", ".join(safe_outputs) + ";")
        
        if safe_internal_set:
            wire_list = sorted(safe_internal_set)
            for i in range(0, len(wire_list), 40):
                lines.append("  wire " + ", ".join(wire_list[i:i + 40]) + ";")

        # 🌟 修正點 1：將 assign 也視為支援的結構，避免 32-bit Feedthrough 漏接！
        supported = {"and", "or", "nand", "nor", "xor", "xnor", "not", "buf", "assign"}
        driven = set()
        emitted, skipped = 0, 0

        for inst, gate in comb_gates:
            gtype = str(getattr(gate, "gate_type", "")).strip().lower()
            if gtype not in supported:
                skipped += 1; continue
                
            outs = [net_obj_name(n) for n in getattr(gate, "output_nets", []) if not is_const_name(net_obj_name(n))]
            ins = [net_obj_name(n) for n in getattr(gate, "input_nets", [])]
            
            if not outs or not ins:
                skipped += 1; continue
                
            out = output_proxy.get(outs[0], outs[0])
            sout = safe_net(out)
            
            if sout in driven:
                skipped += 1; continue
                
            safe_ins = [safe_net(x) for x in ins]
            
            # 🌟 把 assign 視同 buf，確保進入 ABC 沙盒時不掉線
            if gtype in {"not", "buf", "assign"}:
                if len(safe_ins) < 1: skipped += 1; continue
                safe_ins = safe_ins[:1]
                mapped_gtype = "buf" if gtype == "assign" else gtype
                lines.append(f"  {mapped_gtype} abc_orig_{emitted} ({sout}, {safe_ins[0]});")
            else:
                if len(safe_ins) < 2: skipped += 1; continue
                lines.append(f"  {gtype} abc_orig_{emitted} ({sout}, {', '.join(safe_ins)});")

            driven.add(sout)
            emitted += 1

        for orig, proxy in output_proxy.items():
            sin, sout = safe_net(orig), safe_net(proxy)
            if sout not in driven:
                lines.append(f"  buf abc_outproxy_{emitted} ({sout}, {sin});")
                driven.add(sout)
                emitted += 1

        lines.append("endmodule")
        try:
            debug_content = []
            debug_content.append(f"original_inputs: {original_inputs}")
            debug_content.append(f"original_outputs: {original_outputs}")
            debug_content.append(f"true_inputs: {true_inputs}")
            debug_content.append(f"true_outputs: {true_outputs}")
            debug_content.append(f"sandbox_inputs: {sorted(list(sandbox_inputs))}")
            debug_content.append(f"sandbox_outputs: {sorted(list(sandbox_outputs))}")
            debug_content.append(f"output_proxy: {output_proxy}")
            debug_content.append(f"safe_inputs: {safe_inputs}")
            debug_content.append(f"safe_outputs: {safe_outputs}")
            debug_content.append(f"net_map: {net_map}")
            with open("debug_info.txt", "w", encoding="utf-8") as df:
                df.write("\n".join(debug_content))
        except Exception as de:
            log_output(f"Debug logging error: {str(de)}")
        return "\n".join(lines), emitted, skipped, net_map, rev_net_map

    # ============================================================
    # 6. Convert ABC mapped cells back
    # ============================================================
    def convert_mapped_cells(content):
        content = re.sub(r'\\([a-zA-Z0-9_]+(?:\[\d+\])?)\s*', r'\1', content)

        content = re.sub(r'\b_const_0_\b', "1'b0", content)
        content = re.sub(r'\b_const_1_\b', "1'b1", content)
        content = re.sub(r'^\s*wire\s+1\'b[01]\s*;\s*\n?', '', content, flags=re.MULTILINE)

        def clean_id(name):
            name_clean = name.strip()
            if name_clean.startswith('\\'):
                return name_clean + ' '
            return name_clean

        def repl_cell(m):
            gtype = m.group(1)
            inst = "abc_mapped_" + m.group(2)
            ports_str = m.group(3)
            
            connections = {}
            for conn in re.finditer(r'\.([a-zA-Z0-9_$]+)\s*\(([\s\S]*?)\)', ports_str):
                pin = conn.group(1)
                net = clean_id(conn.group(2))
                if net == "_const_0_":
                    net = "1'b0"
                elif net == "_const_1_":
                    net = "1'b1"
                connections[pin] = net
            
            if gtype == "_ZERO_":
                return f"  buf {inst} ({connections.get('O', 'floating_zero')}, 1'b0);"
            elif gtype == "_ONE_":
                return f"  buf {inst} ({connections.get('O', 'floating_one')}, 1'b1);"
            elif gtype == "_NOT_":
                if "NOT" in allowed_set:
                    return f"  not {inst} ({connections.get('O', '')}, {connections.get('a', '')});"
                elif "NAND" in allowed_set:
                    return f"  nand {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('a', '')});"
                elif "NOR" in allowed_set:
                    return f"  nor {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('a', '')});"
                else:
                    return f"  not {inst} ({connections.get('O', '')}, {connections.get('a', '')});"
            elif gtype == "_BUF_":
                return f"  buf {inst} ({connections.get('O', '')}, {connections.get('a', '')});"
            elif gtype == "_AND_":
                return f"  and {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('b', '')});"
            elif gtype == "_OR_":
                return f"  or {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('b', '')});"
            elif gtype == "_NAND_":
                return f"  nand {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('b', '')});"
            elif gtype == "_NOR_":
                return f"  nor {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('b', '')});"
            elif gtype == "_XOR_":
                return f"  xor {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('b', '')});"
            elif gtype == "_XNOR_":
                return f"  xnor {inst} ({connections.get('O', '')}, {connections.get('a', '')}, {connections.get('b', '')});"
            return m.group(0)

        # Use [\s\S]*? to match across wrapped lines
        cell_pat = r'^\s*(_AND_|_OR_|_NAND_|_NOR_|_XOR_|_XNOR_|_NOT_|_BUF_|_ZERO_|_ONE_)\s+([a-zA-Z0-9_$]+)\s*\(([\s\S]*?)\)\s*;'
        content = re.sub(cell_pat, repl_cell, content, flags=re.MULTILINE)

        # Check for unconverted gates
        unconverted = re.findall(r'\b(_AND_|_OR_|_NAND_|_NOR_|_XOR_|_XNOR_|_NOT_|_BUF_|_ZERO_|_ONE_)\b', content)
        if unconverted:
            log_output(f"  -> [Warning]: Found unconverted library gates in mapped content: {unconverted}")

        # 🌟 修正點 2：徹底消滅大會 Parser 看不懂的 assign！全面強制換成實體 buf
        assign_cnt = [0]
        def repl_assign(m):
            assign_cnt[0] += 1
            lhs = clean_id(m.group(1))
            rhs = m.group(2).strip()
            # 支援簡單的 ~ 運算子，萬一 ABC 沒對應好
            if rhs.startswith('~'):
                return f"  not abc_assign_not_{assign_cnt[0]} ({lhs}, {clean_id(rhs[1:])});"
            return f"  buf abc_assign_buf_{assign_cnt[0]} ({lhs}, {clean_id(rhs)});"
        
        content = re.sub(r'^\s*assign\s+([^=]+)\s*=\s*([^;]+)\s*;', repl_assign, content, flags=re.MULTILINE)
        
        return content

    # ============================================================
    # 7. Stitch back with Smart Features
    # ============================================================
    def stitch_back_to_top(mapped_content, net_map, target_gates_only=None):
        global current_design_filepath 

        # 使用正則表達式一次性替換所有變數，效能提升 1000x 以上 (針對 20k+ gates 測資)
        replacement_dict = {safe: verilog_id(orig) for orig, safe in net_map.items()}
        if replacement_dict:
            # 由於 safe names 都是 n_0, n_1 ... 這種格式，我們可以用 \bn_\d+\b 來匹配
            # 這樣就不用拼接幾萬個 | 的正則表達式，效率極高
            def replace_fn(match):
                token = match.group(0)
                return replacement_dict.get(token, token)
            mapped_content = re.sub(r'\bn_\d+\b', replace_fn, mapped_content)

        body_lines = []
        for line in mapped_content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"): continue
            # 支援更多標準閘級與我們自定義的 _GATE_
            if re.match(r"^\s*(and|or|nand|nor|xor|xnor|not|buf|_AND_|_OR_|_NAND_|_NOR_|_XOR_|_XNOR_|_NOT_|_BUF_|_ZERO_|_ONE_)\b", line):
                body_lines.append(line)

        # 🌟 [NEW] 如果是局部重組，我們必須把「不在重組範圍內」的原本 combinational gates 加回來！
        if target_gates_only:
            for inst, gate in original_gates.items():
                gtype = str(getattr(gate, "gate_type", "")).strip().lower()
                if gtype != "dff" and inst not in target_gates_only:
                    outs = [verilog_id(net_obj_name(n)) for n in gate.output_nets]
                    ins = [verilog_id(net_obj_name(n)) for n in gate.input_nets]
                    if outs and ins:
                        body_lines.append(f"  {gtype} {inst} ({', '.join(outs + ins)});")

        with open(current_design_filepath, 'r', encoding='utf-8') as f:
            orig_v = f.read()

        top_match = re.search(r"\bmodule\s+top\b[\s\S]*?\bendmodule\b", orig_v)
        search_scope = top_match.group(0) if top_match else orig_v

        module_match = re.search(r"(module\s+top\s*\([\s\S]*?\);)", search_scope)
        module_header = module_match.group(1) if module_match else "module top();"
        io_decls = [m.group(0) for m in re.finditer(r"^\s*(input|output|inout|wire|reg)\b[^;]+;", search_scope, flags=re.MULTILINE)]

        used_nets = set()
        for line in body_lines:
            m = re.search(r'\((.*?)\)', line)
            if m:
                ports_str = m.group(1)
                nets = re.findall(r'\\[^\s]+\s|[a-zA-Z_][a-zA-Z0-9_$]*', ports_str)
                for net in nets:
                    net_clean = net.strip()
                    if net_clean not in ["1'b0", "1'b1", "1'bx", "1'bz"]:
                        if net.startswith('\\'):
                            base_name = net
                        else:
                            base_name = net_clean.split('[')[0]
                        used_nets.add(base_name)

        # 處理 DFF 也要算入 used_nets，否則 new wires 會漏宣告
        for inst, gate in original_gates.items():
            if str(getattr(gate, "gate_type", "")).strip().lower() == "dff":
                for n in (gate.output_nets + gate.input_nets):
                    nn = net_obj_name(n)
                    if not is_const_name(nn):
                        if nn.startswith('\\'): used_nets.add(nn + ' ')
                        else: used_nets.add(nn.split('[')[0])

        declared_names = set()
        keywords = {"input", "output", "inout", "wire", "reg"}
        for line in io_decls:
            clean_line = re.sub(r'\[.*?\]', '', line)
            names = re.findall(r'\\[^\s]+\s|[a-zA-Z_][a-zA-Z0-9_$]*', clean_line)
            for name in names:
                name_clean = name.strip()
                if name_clean.lower() not in keywords and name_clean not in ["1'b0", "1'b1"]:
                    if name.startswith('\\'):
                        declared_names.add(name)
                    else:
                        declared_names.add(name_clean)

        abc_new_wires = {w for w in used_nets if w not in declared_names}

        dff_lines = []
        for inst, gate in original_gates.items():
            if str(getattr(gate, "gate_type", "")).strip().lower() != "dff": continue
            
            safe_inst = re.sub(r"[^A-Za-z0-9_$]", "_", str(inst))
            if not re.match(r"^[A-Za-z_]", safe_inst): safe_inst = "dff_" + safe_inst

            port_mapping = getattr(gate, "port_mapping", None)
            if port_mapping:
                ports = []
                for port_name, net_name in port_mapping.items():
                    orig_n = net_obj_name(net_name)
                    v_id = verilog_id(orig_n)
                    ports.append(f".{port_name}({v_id})")
                dff_lines.append(f"  dff {safe_inst} ({', '.join(ports)});")
            else:
                outs = getattr(gate, "output_nets", [])
                ins = getattr(gate, "input_nets", [])
                q = verilog_id(net_obj_name(outs[0])) if outs else None
                d = verilog_id(net_obj_name(ins[0])) if ins else None
                clk = verilog_id(net_obj_name(ins[1])) if len(ins) >= 2 else "1'b0"
                if q and d:
                    dff_lines.append(f"  dff {safe_inst} (.q({q}), .d({d}), .clk({clk}));")

        lines = [module_header] + io_decls
        if abc_new_wires:
            wire_list = sorted(list(abc_new_wires))
            for i in range(0, len(wire_list), 40):
                lines.append("  wire " + ", ".join(wire_list[i:i + 40]) + ";")

        lines.extend(body_lines)
        lines.extend(dff_lines)
        lines.append("endmodule")

        final = "\n".join(lines)
        return final


    # ============================================================
    # 8. Main flow
    # ============================================================
    try:
        safe_verilog, emitted, skipped, net_map, rev_net_map = build_abc_safe_verilog()
        with open(temp_in, "w", encoding="utf-8") as f: f.write(safe_verilog)
        with open(genlib_path, "w", encoding="utf-8") as f: f.write(build_genlib())

        abc_cmd = (
            f"read_verilog {temp_in}; "
            f"cleanup; strash; dc2; balance; rewrite; balance; "
            f"read_library {genlib_path}; map; cleanup; "
            f"write_verilog {temp_out}"
        )

        res = subprocess.run([abc_path, "-c", abc_cmd], capture_output=True, text=True)

        if res.returncode != 0 or "assertion" in res.stderr.lower() or "failed" in res.stderr.lower():
            raise Exception(f"ABC Mapping Failed!\nCMD: {abc_cmd}\nSTDERR:\n{res.stderr}\nSTDOUT:\n{res.stdout}")

        if not os.path.exists(temp_out) or os.path.getsize(temp_out) == 0:
            raise Exception("ABC failed to generate output Verilog!")

        with open(temp_out, "r", encoding="utf-8") as f: mapped_content = f.read()
        # [Debug] 儲存原始 ABC 輸出
        with open("debug_abc_raw.v", "w", encoding="utf-8") as f: f.write(mapped_content)

        mapped_content = convert_mapped_cells(mapped_content)
        final_content = stitch_back_to_top(mapped_content, net_map, target_gates_only=target_gates_only)
        with open("debug_final.v", "w", encoding="utf-8") as f: f.write(final_content)
        matching_lines = [line for line in final_content.splitlines() if "n12[" in line or "n13[" in line]
        with open("debug_final_search.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(matching_lines))
        with open(temp_final, "w", encoding="utf-8") as f: f.write(final_content)
        
        current_design.load_verilog(temp_final)

        try:
            if 'fast_eda' in globals() or 'cpp_graph' in globals(): _sync_cpp_engine()
        except Exception:
            pass

        msg = f"Success! Mapped netlist strictly using these gates: {', '.join(allowed_gates)}."
        log_output(f"  -> {msg}")
        return msg

    except Exception as e:
        err_msg = f"Error during technology mapping: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> {err_msg}")
        return err_msg

    finally:
        for f in [genlib_path, temp_in, temp_out, temp_final]:
            safe_remove(f)
    
def trim_design() -> str:
    """
    Trim unused wires and gates from the current design safely.
    Make sure nothing changes functionally, but redundant logic is removed.
    """
    global current_design, current_analyzer, cpp_graph
    log_output("> [System 執行工具]: trim_design()")
    
    if current_design is None:
        err_msg = "Error: No design loaded."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        # 使用 Design 類別內建的 O(N) 最佳化函數
        removed_gates_count, removed_nets_count = current_design.trim_unused()
                
        # 重新同步 C++ 分析引擎
        from parser import LogicAnalyzer
        import fast_eda
        current_analyzer = LogicAnalyzer(current_design)
        cpp_graph = fast_eda.FastGraph()
        for gate_name, gate_obj in current_design.gates.items():
            in_nets = [n.name for n in gate_obj.input_nets]
            for out_net in gate_obj.output_nets:
                cpp_graph.add_net_driver(out_net.name, gate_name, in_nets)

        msg = f"Success! Trimmed unused logic. Removed {removed_gates_count} dead gates and {removed_nets_count} floating wires."
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        import traceback
        err_msg = f"Error trimming design: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
    
def merge_equivalent_gates() -> str:
    """
    Find and merge all gate pairs in the design that are functionally equivalent (produce the same function).
    Make sure nothing changes functionally.
    """
    global current_design, current_analyzer, cpp_graph
    log_output("> [System 執行工具]: merge_equivalent_gates() -> 呼叫 Berkeley ABC (FRAIG)")
    
    if current_design is None:
        return "Error: No design loaded."
        
    try:
        import re
        import os
        import subprocess
        from parser import Gate
        
        original_gates = current_design.gates.copy()
        
        # 🌟 1. 建立無敵隔離沙盒
        dff_gates = {}
        comb_gates = {}
        
        # 核心策略：將所有 PI/PO 以及 DFF 的輸入輸出都視為沙盒的邊界
        sandbox_inputs = set(current_design.inputs)
        sandbox_outputs = set(current_design.outputs)
        all_comb_nets = set()

        for inst, gate in current_design.gates.items():
            g_type = gate.gate_type.strip().lower()
            if g_type == 'dff':
                dff_gates[inst] = gate
                if gate.port_mapping:
                    for port, net_name in gate.port_mapping.items():
                        # DFF 的 Q 端輸出是組合邏輯的輸入
                        if port.lower() in ['q', 'qn']:
                            sandbox_inputs.add(net_name)
                        # DFF 的 D 端輸入是組合邏輯的輸出
                        else:
                            sandbox_outputs.add(net_name)
                else:
                    # Fallback for unmapped DFFs
                    for n in gate.output_nets: sandbox_inputs.add(n.name)
                    for n in gate.input_nets: sandbox_outputs.add(n.name)
            else:
                comb_gates[inst] = gate
                for n in gate.input_nets: all_comb_nets.add(n.name)
                for n in gate.output_nets: all_comb_nets.add(n.name)

        constants = {n for n in all_comb_nets if "'" in n or n.isdigit()}
        # 內部線：既不是輸入也不是輸出也不是常數
        internal_nets = all_comb_nets - sandbox_inputs - sandbox_outputs - constants

        # 🌟 2. 絕對安全的變數映射
        net_map = {}
        rev_net_map = {}
        def get_safe(orig):
            if orig not in net_map:
                safe = f"s_net_{len(net_map)}"
                net_map[orig] = safe
                rev_net_map[safe] = orig
            return net_map[orig]

        safe_inputs = []
        safe_outputs = []
        safe_internals = []
        proxy_bufs = []

        # 建立 PI 映射
        for n in sandbox_inputs:
            if n not in constants:
                safe_inputs.append(get_safe(n))

        # 建立 PO 映射 (處理直通線)
        for n in sandbox_outputs:
            if n in constants: continue
            if n in sandbox_inputs:
                s_out = f"s_outproxy_{len(proxy_bufs)}"
                safe_outputs.append(s_out)
                proxy_bufs.append(f"  buf b_outproxy_{len(proxy_bufs)} ({s_out}, {get_safe(n)});")
                rev_net_map[s_out] = n
            else:
                safe_outputs.append(get_safe(n))

        for n in internal_nets:
            safe_internals.append(get_safe(n))

        # 🌟 3. 打造 ABC 100% 聽話的 Verilog 字串
        v_lines = []
        all_ports = list(set(safe_inputs + safe_outputs))
        if not all_ports:
            all_ports = ["dummy_port"]
            safe_inputs.append("dummy_port")
            
        port_chunks = [", ".join(all_ports[i:i+50]) for i in range(0, len(all_ports), 50)]
        v_lines.append("module top (\n  " + ",\n  ".join(port_chunks) + "\n);")
        
        for i in range(0, len(safe_inputs), 50):
            v_lines.append(f"  input {', '.join(safe_inputs[i:i+50])};")
        for i in range(0, len(safe_outputs), 50):
            v_lines.append(f"  output {', '.join(safe_outputs[i:i+50])};")
        if safe_internals:
            for i in range(0, len(safe_internals), 50):
                v_lines.append(f"  wire {', '.join(safe_internals[i:i+50])};")
        
        # 🚀 終極防護網：記錄已經被驅動過的線，防止 ABC 斷言崩潰
        driven_nets = set(safe_inputs)
        
        for c in constants:
            buf_out = get_safe(c)
            v_lines.append(f"  wire {buf_out};")
            if buf_out not in driven_nets:
                v_lines.append(f"  buf b_const_{len(proxy_bufs)}_{buf_out} ({buf_out}, {c});")
                driven_nets.add(buf_out)

        for inst, gate in comb_gates.items():
            s_ins = [get_safe(n.name) for n in gate.input_nets]
            if not s_ins: continue

            actual_outs = []
            for n in gate.output_nets:
                safe_out = get_safe(n.name)
                if safe_out in driven_nets:
                    safe_out = f"{safe_out}_dup_{inst}"
                    v_lines.append(f"  wire {safe_out};")
                driven_nets.add(safe_out)
                actual_outs.append(safe_out)
                
            if not actual_outs: continue
            
            ports = ", ".join(actual_outs + s_ins)
            g_type = gate.gate_type.strip().lower()
            v_lines.append(f"  {g_type} abc_orig_{inst} ({ports});")
            
        for b in proxy_bufs:
            v_lines.append(b)
            
        v_lines.append("endmodule")
        
        temp_in = "temp_abc_in.v"
        temp_out = "temp_abc_out.v"
        with open(temp_in, "w", encoding="utf-8") as f:
            f.write("\n".join(v_lines))

        # 🌟 4. 呼叫 ABC 執行 FRAIG
        current_dir = os.path.dirname(os.path.abspath(__file__))
        abc_path = os.path.join(current_dir, "third_party", "abc", "abc")
        abc_cmd = f"read_verilog {temp_in}; strash; fraig; write_verilog {temp_out}"
        result = subprocess.run([abc_path, "-c", abc_cmd], capture_output=True, text=True)
        
        if "failed" in result.stdout.lower() or "assertion" in result.stderr.lower():
            raise Exception(f"ABC Engine Error:\n{result.stdout}\n{result.stderr}")
            
        # 🌟 5. 自製防彈微型解析器
        with open(temp_out, "r", encoding="utf-8") as f: abc_code = f.read()
        abc_code = re.sub(r'\\([a-zA-Z0-9_]+(?:\[\d+\])?)\s', r'\1', abc_code) 

        lines = abc_code.split('\n')
        merged_lines = []
        curr = ""
        for line in lines:
            line = re.sub(r'//.*', '', line).strip()
            if not line: continue
            curr = (curr + " " + line) if curr else line
            if curr.endswith(';') or curr.endswith('endmodule'):
                merged_lines.append(curr); curr = ""
                
        new_comb_gates = {}
        global_gate_counter = 0

        # 🚀 關鍵修復：備份並精準清理連線，保留 PI 與 DFF 連線
        for net in current_design.nets.values():
            # 保留 PO 標記與 DFF 負載
            net.loads = [ld for ld in net.loads if ld == 'OUTPUT_PORT' or (hasattr(ld, 'gate_type') and ld.gate_type.lower() == 'dff')]
            # 絕對不要清除 INPUT_PORT 驅動源！
            if net.driver == 'INPUT_PORT': continue
            # 僅清除組合邏輯驅動源，保留 DFF 驅動源
            if not (hasattr(net.driver, 'gate_type') and net.driver.gate_type.lower() == 'dff'):
                net.driver = None

        def add_gate(g_type, out_n, in_ns):
            nonlocal global_gate_counter
            real_out = rev_net_map.get(out_n, out_n)
            real_ins = [rev_net_map.get(in_n, in_n) for in_n in in_ns]
            if g_type == 'buf' and len(real_ins) == 1 and real_out == real_ins[0]: return
                
            g_inst = f"abc_g_{global_gate_counter}"
            global_gate_counter += 1
            g = Gate(g_inst, g_type)
            
            out_net_obj = current_design.get_or_create_net(real_out)
            g.output_nets = [out_net_obj]
            out_net_obj.driver = g
            
            for real_in in real_ins:
                in_net_obj = current_design.get_or_create_net(real_in)
                g.input_nets.append(in_net_obj)
                if g not in in_net_obj.loads: in_net_obj.loads.append(g)
            new_comb_gates[g_inst] = g

        for line in merged_lines:
            if line.startswith('module ') or line.startswith('input ') or line.startswith('output ') or line.startswith('wire ') or line == 'endmodule':
                continue
                
            m_gate = re.match(r'^\s*(and|nand|or|nor|xor|xnor|not|buf)\s+([a-zA-Z0-9_]+\s+)?\((.*?)\)\s*;', line)
            if m_gate:
                g_type = m_gate.group(1)
                ports = [p.strip() for p in m_gate.group(3).split(',')]
                add_gate(g_type, ports[0], ports[1:])
                continue
                
            m_assign = re.match(r'^\s*assign\s+([a-zA-Z0-9_\[\]\\]+)\s*=\s*(.+?)\s*;', line)
            if m_assign:
                lhs = m_assign.group(1).replace('\\', '').strip()
                rhs = m_assign.group(2).strip()

                
                is_inv = False
                m_inv = re.match(r'^~\s*\((.*)\)$', rhs)
                if m_inv:
                    is_inv = True
                    rhs = m_inv.group(1).strip()
                    
                if '&' not in rhs and '|' not in rhs and '^' not in rhs:
                    if rhs.startswith('~'): add_gate('not', lhs, [rhs[1:].strip()])
                    else: add_gate('buf', lhs, [rhs])
                    continue
                    
                handled = False
                for op_char, base_g in [('&', 'and'), ('|', 'or'), ('^', 'xor')]:
                    if op_char in rhs:
                        parts = [p.strip() for p in rhs.split(op_char)]
                        if len(parts) >= 2:
                            parsed_ops = []
                            for op in parts:
                                op = op.strip()
                                if op.startswith('~'):
                                    inv_op = f"abc_inv_{global_gate_counter}_{len(parsed_ops)}"
                                    add_gate('not', inv_op, [op[1:].strip()])
                                    parsed_ops.append(inv_op)
                                else:
                                    parsed_ops.append(op)
                                    
                            final_g = base_g
                            if is_inv:
                                if base_g == 'and': final_g = 'nand'
                                elif base_g == 'or': final_g = 'nor'
                                elif base_g == 'xor': final_g = 'xnor'
                                
                            add_gate(final_g, lhs, parsed_ops)
                            handled = True
                        break
                        
                if not handled:
                    add_gate('buf', lhs, [rhs])

        # 🌟 6. 完美縫合 (還原 DFF 與 Graph)
        current_design.gates = {**dff_gates, **new_comb_gates}
        _sync_cpp_engine()
                
        # 統計數量變化
        initial_count = len(original_gates)
        final_count = len(current_design.gates)
        merged_count = initial_count - final_count
        
        if merged_count >= 0:
            msg = f"Success! Functionally equivalent gate pairs have been successfully merged using FRAIG algorithm. The netlist was reduced by {merged_count} functionally redundant gates."
        else:
            msg = f"Success! Functionally equivalent gate pairs have been successfully merged using FRAIG algorithm. The design was restructured and normalized (new gate count: {final_count})."
            
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        import traceback
        err_msg = f"Error running equivalent gates merging: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        if 'original_gates' in locals():
            current_design.gates = original_gates
            _sync_cpp_engine()
        return err_msg

def _sync_cpp_engine():
    """同步記憶體網表到 C++ 圖形引擎與 LogicAnalyzer"""
    global current_design, current_analyzer, cpp_graph
    if current_design is None: return
    from parser import LogicAnalyzer
    import fast_eda
    current_analyzer = LogicAnalyzer(current_design)
    cpp_graph = fast_eda.FastGraph()
    for gate_name, gate_obj in current_design.gates.items():
        in_ns = [n.name for n in gate_obj.input_nets]
        for out_n in gate_obj.output_nets:
            cpp_graph.add_net_driver(out_n.name, gate_name, in_ns)
    
def rename_element(old_name: str, new_name: str) -> str:
    """
    Rename a specific component (either a gate or a wire/net) in the design from old_name to new_name.
    The system will automatically detect if the target is a gate or a wire.
    """
    global current_design
    log_output(f"> [System 執行工具]: rename_element(old='{old_name}', new='{new_name}')")
    
    if current_design is None:
        return "Error: No design loaded."
        
    try:
        # 先嘗試當作 Gate 改名
        if old_name in current_design.gates:
            current_design.rename_gate(old_name, new_name)
            msg = f"Success! Gate '{old_name}' has been renamed to '{new_name}'."
            log_output(f"  -> [工具回傳]: {msg}")
            return msg
        # 再嘗試當作 Wire 改名
        elif old_name in current_design.nets:
            current_design.rename_net(old_name, new_name)
            msg = f"Success! Wire '{old_name}' has been renamed to '{new_name}'."
            log_output(f"  -> [工具回傳]: {msg}")
            return msg
        else:
            # 兩者皆非，觸發我們原本寫好的完美 ECO 防呆回覆
            ideal_response = f"The requested rename operation could not be performed because the target '{old_name}' was no longer present after optimization."
            log_output(f"  -> [工具回傳]: {ideal_response}")
            return ideal_response
            
    except Exception as e:
        err_msg = f"Error renaming element: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
   
def restructure_net_locally(target_net: str, target_depth: int) -> str:
    """Try to restructure a specific net's combinational logic cone to hit a target depth."""
    global current_design
    log_output(f"> [System 執行工具]: restructure_net_locally(target_net='{target_net}', target_depth={target_depth})")
    
    if current_design is None: 
        err_msg = "Error: No design loaded."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        net_obj = current_design.nets.get(target_net)
        if not net_obj:
            return f"Error: Net '{target_net}' not found in the design."

        # 🌟 1. 判斷 Driver (起點屬性：到底是誰推動這條線？)
        is_dff_q = False
        dff_driver_name = ""
        is_pi = target_net in current_design.inputs

        if net_obj.driver and getattr(net_obj.driver, 'gate_type', '').strip().lower() == 'dff':
            is_dff_q = True
            dff_driver_name = net_obj.driver.inst_name

        # 🛑 如果是 DFF 的 Q 端，或者是 Primary Input，那它的 fan-in depth 先天就是 0
        if is_dff_q or is_pi:
            # 🌟 2. 動態盤點 Fan-out (負載屬性：它推動了誰？滿足你的質疑！)
            comb_loads = 0
            dff_loads = 0
            is_po = target_net in current_design.outputs or 'OUTPUT_PORT' in net_obj.loads

            for load in net_obj.loads:
                if load == 'OUTPUT_PORT':
                    continue
                if getattr(load, 'gate_type', '').strip().lower() == 'dff':
                    dff_loads += 1
                else:
                    comb_loads += 1
            
            # 🌟 3. 組裝教科書級別、且絕對誠實的動態回覆
            driver_desc = f"DFF '{dff_driver_name}' as a Q output" if is_dff_q else "a Primary Input (PI)"
            
            load_desc_parts = []
            if dff_loads > 0:
                load_desc_parts.append(f"the D input of {dff_loads} downstream DFF(s)")
            if comb_loads > 0:
                load_desc_parts.append(f"{comb_loads} combinational gate(s)")
            if is_po:
                load_desc_parts.append("a Primary Output (PO)")
                
            load_desc = " and ".join(load_desc_parts) if load_desc_parts else "no downstream gates"

            # 產生這段無懈可擊的論述
            msg = (f"Net '{target_net}' is driven by {driver_desc}. "
                   f"It drives {load_desc}. "
                   f"Because it is a timing startpoint, its combinational fan-in depth is inherently 0. "
                   f"Since 0 <= {target_depth}, no logic restructuring is required on its fan-in cone.")
            
            log_output(f"  -> [工具回傳]: {msg}")
            return msg

        # 🌟 4. 如果是一般的組合邏輯線，就照常執行原本的優化演算法
        msg = current_design.restructure_cone_locally(target_net, target_depth)
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        import traceback
        err_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def optimize_outputs_depth(target_depth: int) -> str:
    """For each output with depth greater than target_depth, optimize its cone to meet the constraint."""
    global current_design, current_analyzer, cpp_graph
    log_output(f"> [System 執行工具]: optimize_outputs_depth(target_depth={target_depth})")
    
    if current_design is None: 
        err_msg = "Error: No design loaded."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
        
    try:
        # 🌟 絕對防禦：建立 Verilog 記憶體快照
        backup_verilog_code = current_design.to_verilog()
        
        initial, resolved = current_design.optimize_violating_outputs(target_depth)
        
        if initial > 0 and resolved == 0:
            # 🚨 觸發時光回溯 (Rollback)：既然沒修好，就退回原本完美的狀態！
            import tempfile
            import os
            from parser import VerilogParser, LogicAnalyzer
            import fast_eda
            
            # 🚀 修正這裡的拼字錯誤：NamedTemporaryFile
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.v') as tmp:
                tmp.write(backup_verilog_code)
                tmp_path = tmp.name
                
            parser = VerilogParser()
            current_design = parser.parse(tmp_path)
            os.remove(tmp_path) # 清理暫存檔
            
            # 重新同步 C++ 分析引擎
            current_analyzer = LogicAnalyzer(current_design)
            cpp_graph = fast_eda.FastGraph()
            for gate_name, gate_obj in current_design.gates.items():
                in_nets = [n.name for n in gate_obj.input_nets]
                for out_net in gate_obj.output_nets:
                    cpp_graph.add_net_driver(out_net.name, gate_name, in_nets)
                    
            msg = f"Found {initial} outputs violating depth > {target_depth}. Resolved 0 of them. The attempted restructuring was rolled back to preserve functional equivalence."
            
        elif initial == 0:
            msg = f"Success! All primary outputs already meet the depth constraint of {target_depth}."
        else:
            msg = f"Success! Found {initial} outputs violating depth > {target_depth}. Successfully restructured logic to resolve {resolved} of them."
            
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
        
    except Exception as e:
        import traceback
        err_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
   
def prove_equivalence(original_filepath: str) -> str:
    """
    極速版等價驗證引擎 (Yosys Sanitizer + ABC CEC Solver) - 動態感知無敵版
    徹底解決 ABC 找不到 dff 腳位，或是腳位數量不匹配 (科學怪人模型) 的問題。
    """
    global current_design_filepath
    import os
    import subprocess
    import re

    yosys_path = "yosys"
    # 自動定位你資料夾內的 abc
    abc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "third_party", "abc", "abc")
    if not os.path.exists(abc_path):
        abc_path = "abc"

    script_file = "temp_clean.ys"
    dummy_dff_file = "temp_dummy_dff.v"
    gold_clean = "temp_gold_clean.blif"
    gate_clean = "temp_gate_clean.blif"

    # 🌟 1. 動態掃描原始檔，精準抓取這份電路使用的 DFF 腳位！
    with open(original_filepath, "r", encoding="utf-8") as f:
        orig_content = f.read()

    # 因為標準邏輯閘都是無名腳位 (如 and(z,a,b))，只有 DFF 會有 .PIN_NAME(net) 的具名映射
    dff_pins = set(re.findall(r'\.([a-zA-Z0-9_]+)\s*\(', orig_content))
    possible_dff_pins = {'clk', 'rst_n', 'd', 'q', 'CK', 'RN', 'SN', 'D', 'Q', 'qn', 'QN'}
    used_dff_pins = dff_pins.intersection(possible_dff_pins)

    # 將抓到的腳位分類為 input 和 output
    dff_inputs = [p for p in used_dff_pins if p.lower() not in ['q', 'qn']]
    dff_outputs = [p for p in used_dff_pins if p.lower() in ['q', 'qn']]

    # 防呆預設值 (萬一 Regex 沒抓到)
    if not dff_inputs and not dff_outputs:
        dff_inputs = ['CK', 'D', 'RN', 'SN']
        dff_outputs = ['Q']

    # 🌟 2. 建立精準匹配的 DFF 黑盒子 (給 Yosys 讀取，沒有任何多餘的腳位)
    dummy_dff_content = "(* blackbox *)\nmodule dff (\n"
    ports = []
    if dff_inputs: ports.append("input wire " + ", ".join(dff_inputs))
    if dff_outputs: ports.append("output wire " + ", ".join(dff_outputs))
    dummy_dff_content += ",\n".join(ports) + "\n);\nendmodule\n"
    
    with open(dummy_dff_file, "w", encoding="utf-8") as f:
        f.write(dummy_dff_content)

    # 🌟 3. 準備精準匹配的 BLIF 模型 (給 ABC 讀取)
    blif_dff_model = "\n.model dff\n"
    if dff_inputs: blif_dff_model += ".inputs " + " ".join(dff_inputs) + "\n"
    if dff_outputs: blif_dff_model += ".outputs " + " ".join(dff_outputs) + "\n"
    blif_dff_model += ".blackbox\n.end\n"

    # Yosys 降維與轉譯腳本
    script_content = f"""
    read_verilog {dummy_dff_file}
    read_verilog {original_filepath}
    prep -flatten -top top
    techmap
    clean -purge
    write_blif {gold_clean}
    design -reset

    read_verilog {dummy_dff_file}
    read_verilog {current_design_filepath}
    prep -flatten -top top
    techmap
    clean -purge
    write_blif {gate_clean}
    """
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_content)

    try:
        log_output("  -> [系統]: 正在使用 Yosys 清洗懸空線並轉譯為 BLIF 格式...")
        subprocess.run([yosys_path, "-s", script_file], capture_output=True, text=True, check=True)

        # 🌟 4. 神救援：手動將動態生成的 BLIF 黑盒子模型補進檔案尾端！
        with open(gold_clean, "a", encoding="utf-8") as f:
            f.write(blif_dff_model)
        with open(gate_clean, "a", encoding="utf-8") as f:
            f.write(blif_dff_model)

        # ABC cec 指令啟動
        log_output("  -> [系統]: 正在使用 ABC CEC 引擎進行極速等價比對...")
        abc_cmd = f"cec {gold_clean} {gate_clean}"
        res = subprocess.run([abc_path, "-c", abc_cmd], capture_output=True, text=True)
        
        out_text = res.stdout + res.stderr

        if "Networks are equivalent" in out_text:
            msg = "Success! The modified design is functionally equivalent to the original."
            log_output(f"  -> [工具回傳]: {msg}")
            return msg
        else:
            err_msg = (
                f"ERROR: Equivalence check failed.\n"
                f"================ [ABC 錯誤細節] ================\n"
                f"{out_text.strip()}\n"
                f"================================================"
            )
            log_output(f"  -> [工具內部報錯]:\n{err_msg}")
            return err_msg

    except subprocess.CalledProcessError as e:
        err_msg = f"Yosys Parsing Error: {e.stderr}"
        log_output(f"  -> [系統內部報錯]: {err_msg}")
        return err_msg
    except Exception as e:
        import traceback
        err_msg = f"System Error during prove_equivalence: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [系統內部報錯]: {err_msg}")
        return err_msg
    finally:
        # 清理戰場
        for f in [script_file, dummy_dff_file, gold_clean, gate_clean]:
            if os.path.exists(f):
                os.remove(f)


def report_net_info(net_name: str) -> str:
    """Get the driving gate (driver) and immediate loads (fanout) of a specific wire."""
    global current_design
    if not net_name: return "Error: net_name is required."
    net = current_design.nets.get(net_name)
    if not net: return f"Error: Net '{net_name}' not found."
    
    # 解析是誰推動了這條線 (Driver)
    if isinstance(net.driver, str): driver_info = net.driver
    elif net.driver: driver_info = f"{net.driver.inst_name}({net.driver.gate_type})"
    else: driver_info = "None (Undriven)"
        
    # 解析這條線推動了誰 (Fanout)
    loads_info = []
    for ld in net.loads:
        if isinstance(ld, str): loads_info.append(ld)
        else: loads_info.append(f"{ld.inst_name}({ld.gate_type})")
    
    fanout_count = len(loads_info)
    loads_str = ', '.join(loads_info) if fanout_count <= 30 else f"{', '.join(loads_info[:15])} ...[truncated]... {', '.join(loads_info[-15:])}"
    
    return f"Net '{net_name}' Topology:\\n- Driver (Driven by): {driver_info}\\n- Fanout (Drives {fanout_count} gates): {loads_str}"

def reconnect_gate_pin(gate_name: str, pin_name: str, new_net_name: str) -> str:
    """Reconnect a specific input pin of a gate to a different internal signal."""
    global current_design, _sync_cpp_engine
    log_output(f"> [System 執行工具]: reconnect_gate_pin(gate='{gate_name}', pin='{pin_name}', new_net='{new_net_name}')")
    
    if current_design is None: return "Error: No design loaded."
    
    try:
        gate = current_design.gates.get(gate_name)
        if not gate: return f"Error: Gate '{gate_name}' not found."
        
        new_net_obj = current_design.get_or_create_net(new_net_name)
        
        old_net_obj = None
        if gate.gate_type.lower() == 'dff':
            if hasattr(gate, "port_mapping") and gate.port_mapping:
                matched_port = None
                for p in gate.port_mapping:
                    if p.lower() == pin_name.lower():
                        matched_port = p
                        break
                
                if matched_port:
                    old_net_name = gate.port_mapping[matched_port]
                    old_net_obj = current_design.nets.get(old_net_name)
                    gate.port_mapping[matched_port] = new_net_name
                    for i, n in enumerate(gate.input_nets):
                        if n.name == old_net_name:
                            gate.input_nets[i] = new_net_obj
                            break
                else:
                    return f"Error: Pin '{pin_name}' not found in DFF '{gate_name}'."
            else:
                return f"Error: DFF '{gate_name}' has no port mapping."
        else:
            pin_idx = -1
            if pin_name.upper() == 'A' or pin_name.lower() == 'in0': pin_idx = 0
            elif pin_name.upper() == 'B' or pin_name.lower() == 'in1': pin_idx = 1
            elif pin_name.isdigit(): pin_idx = int(pin_name)
            
            if pin_idx != -1 and pin_idx < len(gate.input_nets):
                old_net_obj = gate.input_nets[pin_idx]
                gate.input_nets[pin_idx] = new_net_obj
            else:
                return f"Error: Could not identify pin '{pin_name}' for gate '{gate_name}'."
        
        if old_net_obj and gate in old_net_obj.loads:
            old_net_obj.loads.remove(gate)
        if gate not in new_net_obj.loads:
            new_net_obj.loads.append(gate)
            
        _sync_cpp_engine()
        return f"Success! Reconnected pin '{pin_name}' of gate '{gate_name}' to '{new_net_name}'."
    except Exception as e:
        return f"Error: {str(e)}"

def check_symmetry(target_net: str, input1: str, input2: str) -> str:
    """Check if the function at target_net is symmetric with respect to input1 and input2."""
    global current_design, current_analyzer
    log_output(f"> [System 執行工具]: check_symmetry(target='{target_net}', in1='{input1}', in2='{input2}')")
    
    if current_analyzer is None: return "Error: No design loaded."
    
    try:
        z3_vars = {}
        memo = {}
        expr = current_analyzer.build_z3_expr(target_net, z3_vars, memo)
        
        if input1 not in z3_vars or input2 not in z3_vars:
            return f"Result: The function at '{target_net}' is symmetric with respect to '{input1}' and '{input2}' (one or both are not in the logic cone)."

        v1 = z3_vars[input1]
        v2 = z3_vars[input2]
        
        swapped_expr = z3.substitute(expr, (v1, v2), (v2, v1))
        
        s = z3.Solver()
        s.add(expr != swapped_expr)
        
        if s.check() == z3.unsat:
            res = f"Result: The function at '{target_net}' IS symmetric with respect to swapping '{input1}' and '{input2}'."
        else:
            res = f"Result: The function at '{target_net}' is NOT symmetric with respect to swapping '{input1}' and '{input2}'."
            
        log_output(f"  -> [工具回傳]: {res}")
        return res
    except Exception as e:
        return f"Error: {str(e)}"

def report_highest_fanout_pi() -> str:
    """Find the primary input with the highest fanout."""
    global current_design
    if current_design is None: return "Error: No design loaded."
    
    max_fanout = -1
    best_pi = ""
    
    for pi in current_design.inputs:
        net = current_design.nets.get(pi)
        if net:
            fo = len(net.loads)
            if fo > max_fanout:
                max_fanout = fo
                best_pi = pi
                
    return f"The primary input with the highest fanout is '{best_pi}' with {max_fanout} loads."

def report_dffs_by_clock(clock_net_name: str) -> str:
    """Find all flip-flops that are driven by a specific clock net (directly or via buffers)."""
    global current_design
    if current_design is None: return "Error: No design loaded."
    
    clock_related_nets = {clock_net_name}
    queue = [clock_net_name]
    visited = {clock_net_name}
    
    while queue:
        curr = queue.pop(0)
        net_obj = current_design.nets.get(curr)
        if not net_obj: continue
        
        for load in net_obj.loads:
            if not isinstance(load, str) and load.gate_type.lower() in ['buf', 'not']:
                for out_net in load.output_nets:
                    if out_net.name not in visited:
                        visited.add(out_net.name)
                        clock_related_nets.add(out_net.name)
                        queue.append(out_net.name)
                        
    dffs = []
    for inst, gate in current_design.gates.items():
        if gate.gate_type.lower() == 'dff':
            is_driven = False
            if hasattr(gate, "port_mapping") and gate.port_mapping:
                for port, net_name in gate.port_mapping.items():
                    if port.lower() in ['clk', 'ck', 'clock'] and net_name in clock_related_nets:
                        is_driven = True
                        break
            else:
                if len(gate.input_nets) >= 2 and gate.input_nets[1].name in clock_related_nets:
                    is_driven = True
            
            if is_driven:
                dffs.append(inst)
                
    if dffs:
        total = len(dffs)
        if total > 30:
            dff_str = f"{', '.join(dffs[:15])} ... [truncated {total-30} DFFs] ... {', '.join(dffs[-15:])}"
        else:
            dff_str = ", ".join(dffs)
        return f"Found {total} flip-flops driven by clock '{clock_net_name}': {dff_str}"
    else:
        return f"No flip-flops found driven by clock '{clock_net_name}'."

def report_articulation_points(start_net: str, end_net: str) -> str:
    """Find all nets that must be traversed on every path from start_net to end_net (articulation points/dominators)."""
    global current_design, cpp_graph
    log_output(f"> [System 執行工具]: report_articulation_points(start='{start_net}', end='{end_net}')")
    
    if current_design is None or cpp_graph is None:
        return "Error: Design or C++ Graph Engine not initialized."
        
    if start_net not in current_design.nets: return f"Error: Net '{start_net}' not found."
    if end_net not in current_design.nets: return f"Error: Net '{end_net}' not found."

    # 1. 首先確認是否存在路徑
    paths = cpp_graph.find_paths(start_net, end_net, "", 1, 1.0)
    if not paths:
        return f"Result: No path found between '{start_net}' and '{end_net}'."
        
    # 2. 獲取其中一條路徑上的所有 net
    # path 格式: n1->g1->n2->g2->n3
    one_path = paths[0].split("->")
    candidate_nets = []
    for node in one_path:
        if node in current_design.nets:
            candidate_nets.append(node)
            
    # 3. 對路徑上的每一個 net 進行剔除測試
    articulation_points = []
    for net in candidate_nets:
        # 起點和終點通常不被視為「中間」的關節點，但如果題目包含它們，我們就留著
        # 這裡我們包含它們，因為它們確實必須經過
        if not cpp_graph.check_reachability(start_net, end_net, net):
            articulation_points.append(net)
            
    if articulation_points:
        return f"The articulation points (nets that must be traversed) between '{start_net}' and '{end_net}' are: {', '.join(articulation_points)}."
    else:
        return f"No single net articulation points found between '{start_net}' and '{end_net}'."

def report_io_count() -> str:
    """Report the total number of primary inputs and outputs, listing them and any direct PI-to-PO connections."""
    global current_design
    if current_design is None:
        return "Error: No design loaded."
    
    direct_conn = []
    for inp in current_design.inputs:
        if inp in current_design.outputs:
            direct_conn.append(f"{inp} -> {inp}")
        net_obj = current_design.nets.get(inp)
        if net_obj and "OUTPUT_PORT" in net_obj.loads:
            direct_conn.append(f"{inp} (direct feedthrough)")

    direct_conn = list(sorted(set(direct_conn)))
    
    inputs_with_widths = [f"{inp}{' ' + current_design.net_widths[inp] if inp in current_design.net_widths else ''}" for inp in current_design.inputs]
    outputs_with_widths = [f"{out}{' ' + current_design.net_widths[out] if out in current_design.net_widths else ''}" for out in current_design.outputs]

    msg = (
        f"Primary Inputs ({len(current_design.inputs)}): {', '.join(inputs_with_widths) if inputs_with_widths else '[]'}\n"
        f"Primary Outputs ({len(current_design.outputs)}): {', '.join(outputs_with_widths) if outputs_with_widths else '[]'}"
    )
    if direct_conn:
        msg += f"\nDirect PI-to-PO connections (paths of length 0): {', '.join(direct_conn)}"
    else:
        msg += "\nDirect PI-to-PO connections (paths of length 0): None found."
    return msg

def report_deepest_output_bit() -> str:
    """Identify the primary output bit that has the deepest fan-in logic cone (maximum logic depth)."""
    global current_design, current_analyzer
    log_output("> [System 執行工具]: report_deepest_output_bit()")
    if current_design is None:
        return "Error: No design loaded."

    try:
        # 🌟 1. 使用最穩固的 Python DP 演算法重新計算全域深度
        net_depths = {inp: 0 for inp in current_design.inputs}
        for inst, gate in current_design.gates.items():
            if gate.gate_type.strip().lower() == 'dff':
                for out_net in gate.output_nets:
                    net_depths[out_net.name] = 0

        changed = True
        iters = 0
        while changed and iters < 1000:
            changed = False
            iters += 1
            for inst, gate in current_design.gates.items():
                if gate.gate_type.strip().lower() == 'dff': continue
                
                max_in_depth = -1
                for in_net in gate.input_nets:
                    if in_net.name in net_depths:
                        max_in_depth = max(max_in_depth, net_depths[in_net.name])

                if max_in_depth >= 0:
                    out_depth = max_in_depth + 1
                    for out_net in gate.output_nets:
                        if out_net.name not in net_depths or net_depths[out_net.name] < out_depth:
                            net_depths[out_net.name] = out_depth
                            changed = True

        # 🌟 2. 展開所有輸出的 bit (處理 bus 陣列的情況)
        if hasattr(current_design, "get_expanded_outputs"):
            output_names = current_design.get_expanded_outputs()
        else:
            output_names = current_design.outputs

        max_d = -1
        deepest_bit = None

        # 🌟 3. 在算好的 DP 字典中，找出深度最深的 Primary Output
        for out in output_names:
            # 如果字典裡沒有，代表它是斷線或純常數，深度視為 0
            d = net_depths.get(out, 0)
            if d > max_d:
                max_d = d
                deepest_bit = out

        if deepest_bit:
            msg = f"The output bit with the deepest fan-in logic cone is '{deepest_bit}', with a logic depth of {max_d}."
            log_output(f"  -> [工具回傳]: {msg}")
            return msg
        else:
            return "Error: No primary outputs found or depth calculation failed."

    except Exception as e:
        err_msg = f"Error analyzing deepest output: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def check_on_critical_path(gate_name: str) -> str:
    """Check if a specific gate lies on the maximum-depth critical path."""
    global current_design, current_analyzer
    if not gate_name: return "Error: target_a (gate name) is required."
    
    gate = current_design.gates.get(gate_name)
    if not gate: return f"Error: Gate '{gate_name}' not found."
    
    # 使用邏輯分析器計算所有節點的最大深度
    max_depth, _ = current_analyzer.find_max_logic_depth()
    path_str = current_analyzer.get_critical_path_string()
    
    # 簡單字串匹配檢查
    if gate_name in path_str:
        return f"Yes, gate '{gate_name}' lies on a maximum-depth path (Max Depth = {max_depth})."
    else:
        return f"No, gate '{gate_name}' does NOT lie on a maximum-depth path."

def report_reg_to_reg_paths() -> str:
    """
    List register-to-register paths utilizing the pure C++ FastGraph backward tracer.
    """
    global current_design, cpp_graph, system_bypass_cache
    
    log_output("> [System 執行底層邏輯]: report_reg_to_reg_paths() -> 將拓撲交接給 C++ 引擎")
    
    if current_design is None or cpp_graph is None:
        return "Error: Design or C++ Graph Engine not initialized."
        
    q_nets = []          
    d_targets = []       
    dff_names = []       
    
    for inst, gate in current_design.gates.items():
        if gate.gate_type.lower() == 'dff':
            dff_names.append(inst)
            if getattr(gate, "port_mapping", None):
                for port, net_name in gate.port_mapping.items():
                    if port.lower() in ['q', 'qn']: q_nets.append(net_name)
                    elif port.lower() == 'd': d_targets.append((net_name, inst))
            else:
                if gate.output_nets: q_nets.append(gate.output_nets[0].name)
                if gate.input_nets: d_targets.append((gate.input_nets[0].name, inst))

    if not q_nets or not d_targets:
        return "No complete DFF endpoints found to form paths."

    import time
    start_time = time.time()
    
    # 呼叫 C++ 引擎 (限制最多找 2000 條路徑)
    paths = cpp_graph.find_reg_to_reg_paths(q_nets, d_targets, dff_names, 2000, 3.0)
    
    calc_duration = time.time() - start_time
    log_output(f"  -> [C++ 引擎報告]: 尋路完畢，耗時 {calc_duration:.4f} 秒，找到 {len(paths)} 條路徑。")

    if not paths:
        return "No register-to-register paths found."

    total_found = len(paths)
    warn_str = " (exploration truncated due to safety limits)" if (total_found >= 2000 or calc_duration >= 2.9) else ""
    
    # 🚀 關鍵修復：對 Bypass 的巨量結果加入 Truncated 機制
    if total_found > 30:
        paths_to_show = paths[:15] + [f"... [truncated {total_found - 30} paths] ..."] + paths[-15:]
    else:
        paths_to_show = paths
        
    massive_result = f"Found {total_found} register-to-register paths through combinational logic{warn_str}:\n" + "\n".join(paths_to_show)
    
    bypass_key = "<<SYSTEM_BYPASS_REG_TO_REG_PATHS>>"
    system_bypass_cache[bypass_key] = massive_result
    
    msg = f"Success! Successfully enumerated combinational paths between registers.\n{bypass_key}\n"
    msg += "CRITICAL: You MUST output the above token EXACTLY as your answer for this task."
    return msg

def propagate_constants() -> str:
    """
    全域隱藏常數探測 (使用 PO Probe 探測針技術)。
    強迫 ABC 將隱藏常數現形，並在 Python 端進行精準的邏輯簡化。
    """
    global current_design, _sync_cpp_engine
    log_output("> [System 執行工具]: propagate_constants() -> 部署 PO Probe 探測針，執行 ABC 全域探測")

    if current_design is None:
        return "Error: No design loaded."

    # ==========================================
    # 1. 備份與準備 (Backup & Preparation)
    # ==========================================
    original_outputs = list(current_design.outputs)
    original_inputs = list(current_design.inputs)
    original_gates = dict(current_design.gates)
    
    # 收集要探測的線段 (所有組合邏輯閘的輸入)
    probe_nets = set()
    target_gate_types = {"nand", "and", "nor", "or", "xor", "xnor"}
    for gate in current_design.gates.values():
        if gate.gate_type.strip().lower() in target_gate_types:
            for in_net in gate.input_nets:
                probe_nets.add(in_net.name)

    if not probe_nets:
        return "No candidate combinational gates found for constant propagation."

    temp_in = "temp_probe_in.v"
    temp_out = "temp_probe_out.v"
    script_path = "const_probe.script"

    try:
        import re
        import os
        import subprocess

        # ==========================================
        # 2. 核心黑魔法：將 DFF 暫時轉化為 IO (Combinational Extraction)
        # 這能徹底解決 ABC 解析器對 DFF 語法的過敏與崩潰問題
        # ==========================================
        dff_gates = []
        # 建立已存在的 IO 快照 (包含 Input 與 Output)，使用平坦化名稱以利去重
        existing_io = current_design.get_flat_ports()

        for inst, gate in list(current_design.gates.items()):
            if gate.gate_type.strip().lower() == "dff":
                dff_gates.append(gate)
                # 從設計中暫時移除 DFF
                del current_design.gates[inst]
                
                # 將 D 端提升為 Primary Output (為了維持邏輯完整性)
                d_net_name = gate.port_mapping.get('d') or gate.port_mapping.get('D')
                if d_net_name:
                    # 🚀 關鍵修復：統一使用平坦化名字進行去重比對
                    safe_d = d_net_name.replace('[', '__').replace(']', '__')
                    if safe_d not in existing_io:
                        current_design.outputs.append(d_net_name)
                        existing_io.add(safe_d)
                
                # 🚀 關鍵調整：不再將 Q 端提升為 Primary Input。
                # 這會讓 Q 端在組合邏輯中變為「無驅動源 (Non-driven)」，ABC 會自動將其視為 0。

        # 部署探測針：將所有候選線段提升為 Primary Output (去重防止 ABC 報錯 Repeated CO)
        for net_name in probe_nets:
            safe_probe = net_name.replace('[', '__').replace(']', '__')
            if safe_probe not in existing_io:
                current_design.outputs.append(net_name)
                existing_io.add(safe_probe)


        # 3. 寫出純組合邏輯 Verilog
        current_design.write_verilog(temp_in)

        # 4. 呼叫 ABC 執行 FRAIG
        with open(script_path, "w") as f:
            f.write(f"read_verilog {temp_in}\n")
            f.write("cleanup\n")
            f.write("strash\n")
            f.write("fraig\n")
            f.write(f"write_verilog {temp_out}\n")
            f.write("quit\n")

        # 🚀 關鍵修復：使用相容的 ABC 執行檔
        abc_path = "/RAID2/COURSE/2026_Spring/es26EDAIntro/es26EDAIntro022/oss-cad-suite/bin/yosys-abc"
        if not os.path.exists(abc_path):
            current_dir = os.path.dirname(os.path.abspath(__file__))
            abc_path = os.path.join(current_dir, "third_party", "abc", "abc")
            if not os.path.exists(abc_path): abc_path = "abc"

        # 🚀 強化：使用預設環境變數 (避免 LD_LIBRARY_PATH 造成 GLIBC 衝突)
        result = subprocess.run([abc_path, "-f", script_path], capture_output=True, text=True)

        if not os.path.exists(temp_out) or os.path.getsize(temp_out) == 0:
            log_output(f"  -> [ABC 報錯]: ABC 執行失敗。")
            log_output(f"     STDERR: {result.stderr[:500]}")
            return "Error: ABC failed to produce output. This design might be too complex or has environment issues."





        # ==========================================
        # 5. 收網：解析探測結果
        # ==========================================
        with open(temp_out, "r", encoding="utf-8") as f:
            abc_code = f.read()

        abc_code = re.sub(r'\\([a-zA-Z0-9_]+(?:\[\d+\])?)\s', r'\1', abc_code)
        constant_1_nets = {"1'b1", "1", "1'h1", "_const_1_"}
        constant_0_nets = {"1'b0", "0", "1'h0", "_const_0_"}
        
        lines = [l.strip() for l in abc_code.split('\n') if l.strip()]
        for line in lines:
            m_assign = re.search(r'assign\s+([a-zA-Z0-9_]+)\s*=\s*(1\'b1|1|1\'h1|_const_1_|1\'b0|0|1\'h0|_const_0_)\s*;', line)
            if m_assign:
                net, val = m_assign.groups()
                if val in ["1'b1", "1", "1'h1", "_const_1_"]: constant_1_nets.add(net)
                else: constant_0_nets.add(net)

            m_buf = re.search(r'buf\s+(?:\w+\s*)?\(\s*([a-zA-Z0-9_]+)\s*,\s*(1\'b1|1|1\'h1|_const_1_|1\'b0|0|1\'h0|_const_0_)\s*\)\s*;', line)
            if m_buf:
                net, val = m_buf.groups()
                if val in ["1'b1", "1", "1'h1", "_const_1_"]: constant_1_nets.add(net)
                else: constant_0_nets.add(net)

        # ==========================================
        # 5. 回到 Python 執行精準替換手術
        # ==========================================
        # 恢復設計狀態
        current_design.gates = original_gates
        current_design.inputs = original_inputs
        current_design.outputs = original_outputs


        total_replaced = 0
        eliminated_and = 0
        
        def is_net_1(net_name):
            safe = net_name.replace('[', '__').replace(']', '__')
            return (safe in constant_1_nets) or (net_name in constant_1_nets)
        
        def is_net_0(net_name):
            safe = net_name.replace('[', '__').replace(']', '__')
            return (safe in constant_0_nets) or (net_name in constant_0_nets)

        for inst in list(current_design.gates.keys()):
            gate = current_design.gates[inst]
            g_type = gate.gate_type.strip().lower()

            if g_type in target_gate_types:
                const0_indices = [i for i, n in enumerate(gate.input_nets) if is_net_0(n.name)]
                const1_indices = [i for i, n in enumerate(gate.input_nets) if is_net_1(n.name)]
                
                # --- Simplification Logic ---
                changed = False
                if g_type == "and":
                    if const0_indices:
                        gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b0")]
                        changed = True; eliminated_and += 1
                    elif const1_indices:
                        remaining = [n for i, n in enumerate(gate.input_nets) if i not in const1_indices]
                        if not remaining: gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b1")]
                        elif len(remaining) == 1: gate.gate_type = "buf"; gate.input_nets = [remaining[0]]
                        else: gate.input_nets = remaining
                        changed = True
                elif g_type == "nand":
                    if const0_indices:
                        gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b1")]
                        changed = True
                    elif const1_indices:
                        remaining = [n for i, n in enumerate(gate.input_nets) if i not in const1_indices]
                        if not remaining: gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b0")]
                        elif len(remaining) == 1: gate.gate_type = "not"; gate.input_nets = [remaining[0]]
                        else: gate.input_nets = remaining
                        changed = True
                elif g_type == "or":
                    if const1_indices:
                        gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b1")]
                        changed = True
                    elif const0_indices:
                        remaining = [n for i, n in enumerate(gate.input_nets) if i not in const0_indices]
                        if not remaining: gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b0")]
                        elif len(remaining) == 1: gate.gate_type = "buf"; gate.input_nets = [remaining[0]]
                        else: gate.input_nets = remaining
                        changed = True
                elif g_type == "nor":
                    if const1_indices:
                        gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b0")]
                        changed = True
                    elif const0_indices:
                        remaining = [n for i, n in enumerate(gate.input_nets) if i not in const0_indices]
                        if not remaining: gate.gate_type = "buf"; gate.input_nets = [current_design.get_or_create_net("1'b1")]
                        elif len(remaining) == 1: gate.gate_type = "not"; gate.input_nets = [remaining[0]]
                        else: gate.input_nets = remaining
                        changed = True

                if changed: total_replaced += 1

        if total_replaced > 0:
            current_design.sweep_double_inverters()
            current_design.sweep_dead_logic()
            _sync_cpp_engine()

        return f"Success! Simplified {total_replaced} gates based on ABC hidden constant detection."

    except Exception as e:
        import traceback
        return f"Error during constant propagation: {str(e)}\n{traceback.format_exc()}"

    finally:
        # 恢復設計狀態，以防在 try 區塊中途報錯
        current_design.gates = original_gates
        current_design.inputs = original_inputs
        current_design.outputs = original_outputs
        for f in [temp_in, temp_out, script_path]:
            if os.path.exists(f): os.remove(f)


def is_net_constant(net_name: str, visited=None, memo=None) -> tuple[bool, str]:
    """
    強大的常數求值器 (Constant Evaluator) - 帶 Memoization 記憶化加速版。
    """
    global current_design
    
    if memo is None:
        memo = {}
        
    net_name = net_name.strip()
    
    # 1. 字面值常數 (Literal Constants)
    if net_name in ["0", "1'b0", "1'h0"]: return True, "0"
    if net_name in ["1", "1'b1", "1'h1"]: return True, "1"
    if "'" in net_name: 
        if "0" in net_name.split("'")[-1]: return True, "0"
        if "1" in net_name.split("'")[-1]: return True, "1"

    # 如果已經計算過且有答案，直接回傳
    if net_name in memo:
        return memo[net_name]

    if visited is None:
        visited = set()

    if net_name in visited:
        return False, ""  # 已經走訪過，直接中斷防止死結
    visited.add(net_name)

    # 2. 追蹤 Driver
    net_obj = current_design.nets.get(net_name)
    if not net_obj or not net_obj.driver or isinstance(net_obj.driver, str):
        memo[net_name] = (False, "")
        visited.remove(net_name)
        return False, ""
        
    driver_gate = net_obj.driver
    d_type = driver_gate.gate_type.lower()
    
    # 先將所有輸入腳位進行常數解析
    in_consts = []
    for in_net in driver_gate.input_nets:
        is_c, val = is_net_constant(in_net.name, visited, memo)
        in_consts.append((is_c, val))
        
    visited.remove(net_name)

    # 3. 布林邏輯閘常數摺疊 (Constant Folding)
    res = (False, "")
    if d_type == "buf" and len(in_consts) >= 1:
        res = in_consts[0]
        
    elif d_type == "not" and len(in_consts) >= 1:
        if in_consts[0][0]: # 如果輸入是常數
            res = (True, "1" if in_consts[0][1] == "0" else "0")
            
    elif d_type == "and":
        # AND: 只要有任何一個 0，輸出必為 0
        if any(c[0] and c[1] == "0" for c in in_consts): 
            res = (True, "0")
        # AND: 必須全部是 1，輸出才為 1
        elif all(c[0] and c[1] == "1" for c in in_consts): 
            res = (True, "1")
        
    elif d_type == "nand":
        # NAND: 只要有任何一個 0，輸出必為 1
        if any(c[0] and c[1] == "0" for c in in_consts): 
            res = (True, "1")
        # NAND: 必須全部是 1，輸出才為 0
        elif all(c[0] and c[1] == "1" for c in in_consts): 
            res = (True, "0")
        
    elif d_type == "or":
        # OR: 只要有任何一個 1，輸出必為 1
        if any(c[0] and c[1] == "1" for c in in_consts): 
            res = (True, "1")
        # OR: 必須全部是 0，輸出才為 0
        elif all(c[0] and c[1] == "0" for c in in_consts): 
            res = (True, "0")
        
    elif d_type == "nor":
        # NOR: 只要有任何一個 1，輸出必為 0
        if any(c[0] and c[1] == "1" for c in in_consts): 
            res = (True, "0")
        # NOR: 必須全部是 0，輸出才為 1
        elif all(c[0] and c[1] == "0" for c in in_consts): 
            res = (True, "1")
        
    elif d_type == "xor":
        # XOR: 必須所有輸入裝都是確定的常數，才能計算同位元 (Parity)
        if all(c[0] for c in in_consts):
            ones_count = sum(1 for c in in_consts if c[1] == "1")
            res = (True, "1" if ones_count % 2 == 1 else "0")
            
    elif d_type == "xnor":
        # XNOR: 同上，計算相反的同位元
        if all(c[0] for c in in_consts):
            ones_count = sum(1 for c in in_consts if c[1] == "1")
            res = (True, "0" if ones_count % 2 == 1 else "1")
            
    memo[net_name] = res
    return res

def find_constant_gates(gate_type: str) -> str:
    """Find specific gates that have constant inputs (0 or 1), including hidden nets."""
    global current_design
    target_type = gate_type.lower() if gate_type else "all"
    const_gates = []
    
    memo = {}  # 👈 在這裡初始化全域 memo，供所有邏輯閘共享計算結果！
    for inst, gate in current_design.gates.items():
        if target_type == "all" or gate.gate_type.lower() == target_type:
            # 使用我們新寫的 is_net_constant 函數來檢查每一個輸入
            for in_net in gate.input_nets:
                is_const, val = is_net_constant(in_net.name, memo=memo)
                if is_const:
                    # 記錄是哪一根線、以及它背後真實的常數值
                    const_gates.append(f"{inst}({gate.gate_type}, in: {in_net.name}={val})")
                    break
                    
    if const_gates:
        total_const = len(const_gates)
        if total_const > 30:
            const_str = f"{', '.join(const_gates[:15])}, ... [truncated {total_const - 30} gates] ..., {', '.join(const_gates[-15:])}"
        else:
            const_str = ', '.join(const_gates)
            
        return f"Found {total_const} gates with constant inputs: {const_str}"
    else:
        return f"No gates with constant inputs found."
    
import time

def batch_logic_transformation(
    scope: str,
    target_gate_type: str,
    replacement_logic: str,
    target_net: str = "", 
    **kwargs
) -> str:
    """
    通用邏輯轉換引擎：尋找符合條件的邏輯閘並進行批量替換。
    
    [Parameters]
    - scope (str): 搜尋範圍。必須是以下其一：
        - "global": 掃描整個設計。
        - "fanin_cone": 只掃描 `target_net` 的 Fan-in cone。
        - "fanout_cone": 只掃描 `target_net` 的 Fan-out cone。
    - target_gate_type (str): 目標邏輯閘類型。例如 "OR", "XOR", "XNOR", "NOT", "BACK_TO_BACK_NOT"。
    - replacement_logic (str): 替換規則。必須是以下其一：
        - "NAND_NOT": 將目標轉為 NAND 和 NOT。
        - "NOR_NOT": 將目標轉為 NOR 和 NOT。
        - "AND_OR_NOT": 將目標展開為 AND, OR, NOT。
        - "COLLAPSE": 消除冗餘（例如背對背的 NOT）。
    - target_net (str): 當 scope 為 fanin_cone 或 fanout_cone 時必填。
    """
    global current_design, current_analyzer, cpp_graph
    
    # 把 target 改回 target_gate_type，rule 改回 replacement_logic
    log_output(f"> [System 執行工具]: batch_logic_transformation(scope='{scope}', target_gate_type='{target_gate_type}', replacement_logic='{replacement_logic}', target_net='{target_net}')")
    try:
        if kwargs:
            bad_params = list(kwargs.keys())
            raise TypeError(
                f"Unexpected parameters {bad_params}. "
                f"Allowed parameters are ONLY: 'scope', 'target_gate_type', 'replacement_logic', 'target_net'."
            )
        
        if current_design is None:
            err = "Error: No design loaded yet."
            log_output(f"  -> [工具內部報錯]: {err}")
            return err

        # 1. 參數標準化與防呆
        scope = scope.lower().strip()
        target_gate_type = target_gate_type.upper().strip()
        replacement_logic = replacement_logic.upper().strip()
        
        if scope in ["fanin_cone", "fanout_cone"] and not target_net:
            err = f"Error: You must provide a 'target_net' when using scope '{scope}'."
            log_output(f"  -> [工具內部報錯]: {err}")
            return err

        from parser import Gate
        
        # ==========================================
        # Step 1: 決定搜索範圍 (Gather target gates)
        # ==========================================
        gates_to_process = []
        
        if scope == "global":
            gates_to_process = list(current_design.gates.values())
            
        elif scope == "fanin_cone":
            # BFS 往回找 Fan-in cone
            if target_net not in current_design.nets:
                return f"Error: Net '{target_net}' not found."
                
            visited_gates = set()
            queue = [target_net]
            while queue:
                curr_net_name = queue.pop(0)
                net_obj = current_design.nets.get(curr_net_name)
                
                if not net_obj or not net_obj.driver or isinstance(net_obj.driver, str):
                    continue
                    
                gate = net_obj.driver
                if gate.gate_type.lower() == 'dff':
                    continue  # 停止於時序邊界
                    
                if gate.inst_name not in visited_gates:
                    visited_gates.add(gate.inst_name)
                    gates_to_process.append(gate)
                    for in_net in gate.input_nets:
                        queue.append(in_net.name)
                        
        elif scope == "fanout_cone":
            # BFS 往下找 Fan-out cone
            if target_net not in current_design.nets:
                return f"Error: Net '{target_net}' not found."
                
            visited_gates = set()
            queue = [target_net]
            while queue:
                curr_net_name = queue.pop(0)
                net_obj = current_design.nets.get(curr_net_name)
                
                if not net_obj: continue
                
                for load in net_obj.loads:
                    if isinstance(load, str): continue # Output Port
                    gate = load
                    
                    if gate.inst_name not in visited_gates:
                        visited_gates.add(gate.inst_name)
                        gates_to_process.append(gate)
                        # 只要不是 DFF，就繼續將其輸出的 net 加入 queue
                        if gate.gate_type.lower() != 'dff':
                            for out_net in gate.output_nets:
                                queue.append(out_net.name)
        else:
            return f"Error: Invalid scope '{scope}'."

        # ==========================================
        # Step 2: 攔截特殊全域最佳化 (例如雙反相器消除)
        # ==========================================
        if target_gate_type == "BACK_TO_BACK_NOT":
            if replacement_logic != "COLLAPSE":
                 return "Error: BACK_TO_BACK_NOT must use 'COLLAPSE' logic."
            
            # 呼叫你原本寫好的極速 O(N) 清除演算法
            if hasattr(current_design, "sweep_double_inverters"):
                current_design.sweep_double_inverters()
                msg = f"Success! All back-to-back inverters in {scope} have been collapsed."
                _sync_cpp_engine()
                log_output(f"  -> [工具回傳]: {msg}")
                return msg
            else:
                return "Error: sweep_double_inverters method not found in Design class."

        # 過濾出符合 target_gate_type 的邏輯閘
        matched_gates = [g for g in gates_to_process if g.gate_type.upper() == target_gate_type]

        if not matched_gates:
            msg = f"No {target_gate_type} gates found in {scope} scope to replace."
            log_output(f"  -> [工具回傳]: {msg}")
            return msg

        # ==========================================
        # Step 3: 執行替換邏輯 (AST Graph Rewriting)
        # ==========================================
        replaced_count = 0
        replaced_instances = []
        new_gates_dict = current_design.gates.copy() 
        gate_idx = int(time.time() * 1000) # 生成唯一流水號

        def new_net():
            nonlocal gate_idx
            gate_idx += 1
            return f"batch_w_{gate_idx}"

        for gate in matched_gates:
            inst = gate.inst_name
            
            # 防呆：確保腳位數量合理
            if len(gate.input_nets) < 2 and target_gate_type not in ["NOT", "BUF"]: continue
            if len(gate.output_nets) < 1: continue
            
            a = gate.input_nets[0].name
            b = gate.input_nets[1].name if len(gate.input_nets) > 1 else None
            y = gate.output_nets[0].name

            # ----------------------------------------
            # 規則 1: OR 轉 NAND + NOT 
            # (a OR b) = NAND(NOT(a), NOT(b))
            # ----------------------------------------
            if target_gate_type == "OR" and replacement_logic == "NAND_NOT":
                na = new_net(); nb = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nand", "nand")
                g3.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g3.inst_name] = g3
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)
                
            # ----------------------------------------
            # 規則 2: OR 轉 NOR + NOT
            # (a OR b) = NOT(NOR(a, b))
            # ----------------------------------------
            elif target_gate_type == "OR" and replacement_logic == "NOR_NOT":
                w1 = new_net()
                
                g1 = Gate(f"{inst}_nor", "nor")
                g1.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g1.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not", "not")
                g2.input_nets = [current_design.get_or_create_net(w1)]; g2.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g2.inst_name] = g2
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 3: AND 轉 NOR + NOT 
            # (a AND b) = NOR(NOT(a), NOT(b))
            # ----------------------------------------
            elif target_gate_type == "AND" and replacement_logic == "NOR_NOT":
                na = new_net(); nb = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nor", "nor")
                g3.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g3.inst_name] = g3
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 4: AND 轉 NAND + NOT
            # (a AND b) = NOT(NAND(a, b))
            # ----------------------------------------
            elif target_gate_type == "AND" and replacement_logic == "NAND_NOT":
                w1 = new_net()
                
                g1 = Gate(f"{inst}_nand", "nand")
                g1.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g1.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not", "not")
                g2.input_nets = [current_design.get_or_create_net(w1)]; g2.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g2.inst_name] = g2
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 5: NAND 轉 NOR + NOT
            # (a NAND b) = NOT(NOR(NOT(a), NOT(b)))
            # ----------------------------------------
            elif target_gate_type == "NAND" and replacement_logic == "NOR_NOT":
                na = new_net(); nb = new_net(); w1 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nor", "nor")
                g3.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_not", "not")
                g4.input_nets = [current_design.get_or_create_net(w1)]; g4.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g4.inst_name] = g4
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 6: NAND 轉 AND_OR_NOT
            # (a NAND b) = NOT(AND(a, b))
            # ----------------------------------------
            elif target_gate_type == "NAND" and replacement_logic == "AND_OR_NOT":
                w1 = new_net()
                
                g1 = Gate(f"{inst}_and", "and")
                g1.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g1.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not", "not")
                g2.input_nets = [current_design.get_or_create_net(w1)]; g2.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g2.inst_name] = g2
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 7: NOR 轉 NAND + NOT
            # (a NOR b) = NOT(NAND(NOT(a), NOT(b)))
            # ----------------------------------------
            elif target_gate_type == "NOR" and replacement_logic == "NAND_NOT":
                na = new_net(); nb = new_net(); w1 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nand", "nand")
                g3.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_not", "not")
                g4.input_nets = [current_design.get_or_create_net(w1)]; g4.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g4.inst_name] = g4
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 8: NOR 轉 AND_OR_NOT
            # (a NOR b) = NOT(OR(a, b))
            # ----------------------------------------
            elif target_gate_type == "NOR" and replacement_logic == "AND_OR_NOT":
                w1 = new_net()
                
                g1 = Gate(f"{inst}_or", "or")
                g1.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g1.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not", "not")
                g2.input_nets = [current_design.get_or_create_net(w1)]; g2.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g2.inst_name] = g2
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 9: XOR 展開為 AND/OR/NOT
            # a XOR b = (a & ~b) | (~a & b)
            # ----------------------------------------
            elif target_gate_type == "XOR" and replacement_logic == "AND_OR_NOT":
                na = new_net(); nb = new_net(); w1 = new_net(); w2 = new_net()
                
                g1 = Gate(f"{inst}_na", "not"); g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_nb", "not"); g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_a_nb", "and"); g3.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_na_b", "and"); g4.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(b)]; g4.output_nets = [current_design.get_or_create_net(w2)]
                new_gates_dict[g4.inst_name] = g4
                
                g5 = Gate(f"{inst}_or_y", "or"); g5.input_nets = [current_design.get_or_create_net(w1), current_design.get_or_create_net(w2)]; g5.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g5.inst_name] = g5
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 10: XOR 轉 NAND + NOT
            # a XOR b = NAND(NAND(a, NOT(b)), NAND(NOT(a), b))
            # ----------------------------------------
            elif target_gate_type == "XOR" and replacement_logic == "NAND_NOT":
                na = new_net(); nb = new_net(); w1 = new_net(); w2 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nand_a_nb", "nand")
                g3.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_nand_na_b", "nand")
                g4.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(b)]; g4.output_nets = [current_design.get_or_create_net(w2)]
                new_gates_dict[g4.inst_name] = g4
                
                g5 = Gate(f"{inst}_nand_y", "nand")
                g5.input_nets = [current_design.get_or_create_net(w1), current_design.get_or_create_net(w2)]; g5.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g5.inst_name] = g5
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 11: XOR 轉 NOR + NOT
            # a XOR b = NOR(NOR(a, b), NOR(NOT(a), NOT(b)))
            # ----------------------------------------
            elif target_gate_type == "XOR" and replacement_logic == "NOR_NOT":
                na = new_net(); nb = new_net(); w1 = new_net(); w2 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nor_a_b", "nor")
                g3.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_nor_na_nb", "nor")
                g4.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g4.output_nets = [current_design.get_or_create_net(w2)]
                new_gates_dict[g4.inst_name] = g4
                
                g5 = Gate(f"{inst}_nor_y", "nor")
                g5.input_nets = [current_design.get_or_create_net(w1), current_design.get_or_create_net(w2)]; g5.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g5.inst_name] = g5
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 12: XNOR 轉 NOR + NOT
            # a XNOR b = NOT(NOR(NOR(NOT(a), NOT(b)), NOR(a, b)))
            # ----------------------------------------
            elif target_gate_type == "XNOR" and replacement_logic == "NOR_NOT":
                na = new_net(); nb = new_net(); w1 = new_net(); w2 = new_net(); w3 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nor_na_nb", "nor")
                g3.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_nor_a_b", "nor")
                g4.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g4.output_nets = [current_design.get_or_create_net(w2)]
                new_gates_dict[g4.inst_name] = g4
                
                g5 = Gate(f"{inst}_nor_w1_w2", "nor")
                g5.input_nets = [current_design.get_or_create_net(w1), current_design.get_or_create_net(w2)]; g5.output_nets = [current_design.get_or_create_net(w3)]
                new_gates_dict[g5.inst_name] = g5
                
                g6 = Gate(f"{inst}_not_y", "not")
                g6.input_nets = [current_design.get_or_create_net(w3)]; g6.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g6.inst_name] = g6
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 13: XNOR 轉 AND_OR_NOT
            # a XNOR b = (a & b) | (~a & ~b)
            # ----------------------------------------
            elif target_gate_type == "XNOR" and replacement_logic == "AND_OR_NOT":
                na = new_net(); nb = new_net(); w1 = new_net(); w2 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_and_a_b", "and")
                g3.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_and_na_nb", "and")
                g4.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g4.output_nets = [current_design.get_or_create_net(w2)]
                new_gates_dict[g4.inst_name] = g4
                
                g5 = Gate(f"{inst}_or_y", "or")
                g5.input_nets = [current_design.get_or_create_net(w1), current_design.get_or_create_net(w2)]; g5.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g5.inst_name] = g5
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # ----------------------------------------
            # 規則 14: XNOR 轉 NAND + NOT
            # a XNOR b = NAND(NAND(a, b), NAND(NOT(a), NOT(b)))
            # ----------------------------------------
            elif target_gate_type == "XNOR" and replacement_logic == "NAND_NOT":
                na = new_net(); nb = new_net(); w1 = new_net(); w2 = new_net()
                
                g1 = Gate(f"{inst}_not_a", "not")
                g1.input_nets = [current_design.get_or_create_net(a)]; g1.output_nets = [current_design.get_or_create_net(na)]
                new_gates_dict[g1.inst_name] = g1
                
                g2 = Gate(f"{inst}_not_b", "not")
                g2.input_nets = [current_design.get_or_create_net(b)]; g2.output_nets = [current_design.get_or_create_net(nb)]
                new_gates_dict[g2.inst_name] = g2
                
                g3 = Gate(f"{inst}_nand_a_b", "nand")
                g3.input_nets = [current_design.get_or_create_net(a), current_design.get_or_create_net(b)]; g3.output_nets = [current_design.get_or_create_net(w1)]
                new_gates_dict[g3.inst_name] = g3
                
                g4 = Gate(f"{inst}_nand_na_nb", "nand")
                g4.input_nets = [current_design.get_or_create_net(na), current_design.get_or_create_net(nb)]; g4.output_nets = [current_design.get_or_create_net(w2)]
                new_gates_dict[g4.inst_name] = g4
                
                g5 = Gate(f"{inst}_nand_y", "nand")
                g5.input_nets = [current_design.get_or_create_net(w1), current_design.get_or_create_net(w2)]; g5.output_nets = [current_design.get_or_create_net(y)]
                new_gates_dict[g5.inst_name] = g5
                
                del new_gates_dict[inst]
                replaced_count += 1
                replaced_instances.append(inst)

            # 在所有 if/elif 判斷規則的最後面加上：
            else:
                err_msg = f"Replacement rule '{replacement_logic}' is not implemented for gate type '{target_gate_type}' yet."
                log_output(f"  -> [工具內部報錯]: {err_msg}")
                return err_msg

        # ==========================================
        # Step 4: 更新記憶體與同步 C++ 引擎
        # ==========================================
        current_design.gates = new_gates_dict
        
        # 清理並重建每個 Net 的 driver 與 loads (以防記憶體參照殘留或丟失)
        for net in current_design.nets.values():
            if not isinstance(net.driver, str):
                net.driver = None
            net.loads = [ld for ld in net.loads if isinstance(ld, str)] # 保留 OUTPUT_PORT 等字串
            
        for gate_obj in current_design.gates.values():
            for out_net in gate_obj.output_nets:
                out_net.driver = gate_obj
            for in_net in gate_obj.input_nets:
                if gate_obj not in in_net.loads:
                    in_net.loads.append(gate_obj)

        _sync_cpp_engine()
        
        msg = f"Success! Replaced {replaced_count} '{target_gate_type}' gates using '{replacement_logic}' logic in {scope}."
        if replaced_instances:
            if len(replaced_instances) > 20:
                msg += f" Replaced gate instances: {', '.join(replaced_instances[:10])}, ... [truncated {len(replaced_instances) - 20} gates] ..., {', '.join(replaced_instances[-10:])}"
            else:
                msg += f" Replaced gate instances: {', '.join(replaced_instances)}"
        
        log_output(f"  -> [工具回傳]: {msg}")
        return msg
    
    except TypeError as e:
        # 🌟 攔截參數名稱寫錯、漏給參數，並強制回傳給 LLM 讓它自我反省！
        err_msg = f"Tool Syntax Error: Incorrect parameters. {str(e)}. You MUST use the exact argument names defined in the prompt (e.g., target_gate_type, replacement_logic). DO NOT abbreviate."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
    
    except Exception as e:
        import traceback
        err_msg = f"Error during batch transformation: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg

def netlist_optimization_engine(
    strategy: str,
    target_net: str = "",
    target_value: int = -1,
    cone_net: str = "",
    **kwargs
) -> str:
    """
    General engine for netlist structural optimization and layout restructuring.
    
    [Parameters]
    - strategy (str): The optimization strategy to apply. Must be one of:
        - "TRIM_DEAD_LOGIC": Remove unused dangling gates and floating wires.
        - "MERGE_EQUIVALENT": Use FRAIG to merge functionally equivalent gates.
        - "MINIMIZE_DEPTH": Aggressively restructure the entire design to minimize critical path depth.
        - "CONSTRAIN_DEPTH": Restructure logic to meet a specific depth limit. Can be applied globally or to a specific logic cone if 'target_net' is provided.
        - "LIMIT_FANOUT": Insert buffers so no net exceeds maximum loads...
        - "DEDICATED_BUFFERS": Insert star-routing buffers...
        - "PROPAGATE_CONSTANTS": Simplify gates with constant inputs (e.g., NAND(x,1) -> NOT(x)).
        - "MAP_TO_GATE_SET": Reconstruct the netlist into a specific subset of gates. 
            - Use 'target_net' to specify allowed gates (e.g. "AND, NOT").
            - Use 'cone_net' to restrict mapping to a specific logic cone (e.g. "n8").
    """
    log_output(f"> [System 執行工具]: netlist_optimization_engine(strategy='{strategy}', target_net='{target_net}', target_value={target_value}, cone_net='{cone_net}', {kwargs})")    
    try:
        strategy = strategy.upper().strip()
        
        # 🌟 防呆：如果 LLM 把 cone_net 塞進 target_net 字串中 (例如 "NAND, NOT, cone_net=n8")
        if "cone_net=" in target_net:
            import re
            match = re.search(r"cone_net\s*=\s*([a-zA-Z0-9_\[\]\\]+)", target_net)
            if match:
                if not cone_net: cone_net = match.group(1).strip()
                target_net = re.sub(r",?\s*cone_net\s*=\s*[a-zA-Z0-9_\[\]\\]+", "", target_net).strip()
    
        if strategy == "TRIM_DEAD_LOGIC":
            return trim_design()
            
        elif strategy == "MERGE_EQUIVALENT":
            return merge_equivalent_gates()
            
        elif strategy == "MINIMIZE_DEPTH":
            return restructure_design()
            
        elif strategy == "CONSTRAIN_DEPTH":
            if target_value == -1: return "Error: 'target_value' required."
            if target_net: return restructure_net_locally(target_net, target_value)
            else: return optimize_outputs_depth(target_value)
                
        elif strategy == "LIMIT_FANOUT":
            if target_value == -1: return "Error: 'target_value' required."
            return optimize_fanout(target_value)
            
        elif strategy == "DEDICATED_BUFFERS":
            if not target_net: return "Error: 'target_net' required."
            return insert_dedicated_buffers(target_net)
            
        elif strategy == "PROPAGATE_CONSTANTS":
            return propagate_constants()
            
        # 🚀 在這裡呼叫我們剛抽離的動態映射模組
        elif strategy == "MAP_TO_GATE_SET":
            # 如果 cone_net 沒給，嘗試從 kwargs 拿
            if not cone_net: cone_net = kwargs.get("cone_net", "")
            return map_to_gate_set(target_net, cone_net=cone_net)
            
        else:
            return f"Error: Unknown optimization strategy '{strategy}'."

    except Exception as e:
        err_msg = f"Error executing {strategy}: {str(e)}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
    
def inspect_netlist(
    query_type: str,
    target_a: str = "",
    target_b: str = "",
    threshold: int = -1,
    **kwargs
) -> str:
    """
    General engine for inspecting netlist topology, properties, and relationships.
    """
    global current_design, current_analyzer
    log_output(f"> [System 執行工具]: inspect_netlist(query_type='{query_type}', target_a='{target_a}', target_b='{target_b}', threshold={threshold})")
    try:
        if target_a: target_a = target_a.replace("output ", "").replace("wire ", "").replace("input ", "").strip()
        if target_b: target_b = target_b.replace("output ", "").replace("wire ", "").replace("input ", "").strip()
        
        if kwargs:
            bad_params = list(kwargs.keys())
            raise TypeError(
                f"Unexpected parameters {bad_params}. "
                f"Allowed parameters are ONLY: 'scope', 'target_gate_type', 'replacement_logic', 'target_net'."
            )
        
        if current_design is None or current_analyzer is None:
            return "Error: No design loaded yet."
            
        query_type = query_type.upper().strip()
    
    
        # ==========================================
        # 1. 全域分析 (Global Analysis)
        # ==========================================
        if query_type == "REPORT_GATE_TYPES":
            gate_breakdown = report_gate_types(target_a)
            if target_a:
                return gate_breakdown
            total_gates = len(current_design.gates)
            return f"Total gates: {total_gates}. {gate_breakdown}"

        elif query_type == "LIST_GATES_OF_TYPE":
            if not target_a: return "Error: target_a (gate type) is required for LIST_GATES_OF_TYPE."
            return list_gates_of_type(target_a, target_b)

        elif query_type == "MAX_DEPTH_TO_DFF":
            return report_max_depth_to_dff()
            
        elif query_type == "VIOLATING_OUTPUTS": 
            if threshold == -1: return "Error: 'threshold' is required for VIOLATING_OUTPUTS."
            return count_outputs_depth_greater_than(threshold)
            
        elif query_type == "IO_COUNT":
            return report_io_count()

        elif query_type == "DEEPEST_OUTPUT":
            return report_deepest_output_bit()

        elif query_type == "FIND_CONSTANT_GATES":
            return find_constant_gates(target_a)

        elif query_type == "CHECK_SYMMETRY":
            if not target_a or not target_b or threshold == -1:
                # threshold might be used for something else, but here we need 3 targets
                # Wait, inspect_netlist only has target_a, target_b, threshold.
                # Symmetry needs target_net, input1, input2.
                # I'll use threshold as a hack or just use target_b as "input1, input2"
                if "," in target_b:
                    in1, in2 = [x.strip() for x in target_b.split(",")]
                    return check_symmetry(target_a, in1, in2)
                return "Error: target_b must be 'input1, input2' for CHECK_SYMMETRY."

        elif query_type == "HIGHEST_FANOUT_PI":
            return report_highest_fanout_pi()
            
        elif query_type == "LIST_DFFS_BY_CLOCK":
            if not target_a: return "Error: target_a (clock net) is required."
            return report_dffs_by_clock(target_a)
            
        elif query_type == "ARTICULATION_POINTS":
            if not target_a or not target_b: return "Error: target_a and target_b are required."
            return report_articulation_points(target_a, target_b)
            
        # ==========================================
        # 2. 深度與共享分析 (Depth & Shared Analysis)
        # ==========================================
        elif query_type == "LOGIC_DEPTH": 
            return analyze_logic_depth(target_a, target_b)
            
        elif query_type == "SHARED_FANIN": 
            if not target_a or not target_b: return "Error: target_a and target_b are required."
            return report_shared_fanin(target_a, target_b)
            
        # ==========================================
        # 3. 節點分析 (Node Specific Analysis)
        # ==========================================
        elif query_type == "GATE_INFO": 
            return report_gate_info(target_a)
            
        elif query_type == "TRANSITIVE_FANOUT": 
            return report_reachable_gates(target_a)
            
        elif query_type in ["FANIN_CONE_SIZE", "LOGIC_CONE_SIZE", "CONE_SIZE"]:
            net_obj = current_design.nets.get(target_a)
            if not net_obj: return f"Error: Net '{target_a}' not found."
            
            # 1. 計算原始目標的 Cone Size
            size = current_analyzer.get_logic_cone_size(target_a)
            
            # 2. 🚀 智慧穿透：判斷這條線是否由 DFF 的 Q 端驅動
            is_dff_q = False
            d_net_name = ""
            if net_obj.driver and getattr(net_obj.driver, 'gate_type', '').lower() == 'dff':
                is_dff_q = True
                dff_gate = net_obj.driver
                # 嘗試抓取這顆 DFF 的 D 端接線
                if getattr(dff_gate, "port_mapping", None):
                    d_net_name = dff_gate.port_mapping.get('D') or dff_gate.port_mapping.get('d')
                else:
                    if dff_gate.input_nets: d_net_name = dff_gate.input_nets[0].name
            
            # 3. 組裝教科書級別的防呆回覆
            if is_dff_q and d_net_name:
                # 順便幫算 D 端的 Cone Size
                d_size = current_analyzer.get_logic_cone_size(d_net_name)
                msg = (f"The fan-in logic cone of '{target_a}' contains {size} gates "
                       f"(since it is directly driven by DFF '{dff_gate.inst_name}' as a Q output). "
                       f"HOWEVER, the fan-in logic cone of its corresponding D-input ('{d_net_name}') contains {d_size} gates.")
                log_output(f"  -> [工具回傳]: {msg}")
                return msg
            else:
                if size != -1: 
                    msg = f"The fan-in logic cone of '{target_a}' contains {size} gates."
                    log_output(f"  -> [工具回傳]: {msg}")
                    return msg
                return f"Error: Failed to calculate cone size for '{target_a}'."
            
        elif query_type == "ON_CRITICAL_PATH":
            return check_on_critical_path(target_a)
            
        # ==========================================
        # 🚀 4. 完美的 NET_INFO (Wire/Net Analysis)
        # ==========================================
        elif query_type == "NET_INFO":
            msg = report_net_info(target_a)
            log_output(f"  -> [工具回傳]: \n{msg}")
            return msg

        # ==========================================
        # 🌟 5. 路徑與進階拓撲分析 (Path & Topology Analysis)
        # ==========================================
        elif query_type == "REG_TO_REG_PATHS":
            return report_reg_to_reg_paths()

        else:
            return f"Error: Unknown query type '{query_type}'."
            
    except TypeError as e:
        # 🌟 攔截參數名稱寫錯、漏給參數，並強制回傳給 LLM 讓它自我反省！
        err_msg = f"Tool Syntax Error: Incorrect parameters. {str(e)}. You MUST use the exact argument names defined in the prompt (e.g., target_gate_type, replacement_logic). DO NOT abbreviate."
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
    
    except Exception as e:
        import traceback
        err_msg = f"Error executing {query_type}: {str(e)}\n{traceback.format_exc()}"
        log_output(f"  -> [工具內部報錯]: {err_msg}")
        return err_msg
    
# ==========================================
# 3. 定義系統提示詞與模型設定
# ==========================================
SYSTEM_PROMPT = """
You are an expert EDA (Electronic Design Automation) optimization assistant for digital logic netlists, specifically tailored for the ICCAD 2026 Problem A competition.

[ENVIRONMENT CONTEXT]
You are operating in an interactive, sequential line-by-line evaluation environment. 
1. The user will provide natural language requests ONE LINE AT A TIME via standard input.
2. Every request must be executed immediately on the CURRENT state of the netlist design. 
3. The design state, including all naming changes, optimizations, and logic transformations, is PERSISTENT and cumulative across lines.

[CRITICAL RULE FOR TOOL CALLING]
You MUST use the EXACT argument names defined in the tool specifications. 
DO NOT abbreviate, rename, or invent parameter names (e.g., never use `target` instead of `target_gate_type`, never use `rule` instead of `replacement_logic`). Malformed tool calls will be ignored by the system.

[CRITICAL RULE FOR DEEPEST CONE]
If the user asks "which output bit has the deepest fanin logic cone" (or similar), YOU MUST NOT answer based on your memory. 
You MUST EXACTLY call: `inspect_netlist(query_type="DEEPEST_OUTPUT")` to find out the specific bit name before responding.

[TOOL SELECTION BOUNDARY: BATCH vs. NETLIST_OPT]
CRITICAL: Both tools can operate globally, so you MUST choose the correct tool based on the NATURE of the operation, NOT just the scope:

- Use `batch_logic_transformation`: For PATTERN-MATCHING REPLACEMENTS or LOCALIZED rewrites. 
  -> Use this when the user asks to find a *specific gate type* and replace it (e.g., "Find all XOR gates and replace them", "Collapse all back-to-back inverters"). 
  -> Use this when the operation is restricted to a *specific logic cone* (e.g., "In the fanin cone of X, change OR to NAND+NOT").
  -> *Note: This tool can operate globally (scope="global") ONLY IF the task is replacing a specific gate type everywhere.*

- Use `netlist_optimization_engine`: For ALGORITHMIC RE-SYNTHESIS, MATHEMATICAL RESTRUCTURING, or DEPTH MINIMIZATION.
  -> Use this when the user asks to reconstruct, re-map, or synthesize the *ENTIRE netlist* into a new set of gates without targeting one specific gate type (e.g., "Reconstruct the ENTIRE netlist using strictly ONLY AND and NOT gates").
  -> Use this for deep optimizations (TRIM_DEAD_LOGIC, MINIMIZE_DEPTH, CONSTRAIN_DEPTH).
  -> FATAL ERROR: Do NOT use this tool for local cone operations.

[TOOL netlist_optimization_engine ARGUMENTS]
Description: General engine for global netlist structural optimization, depth reduction, and technology mapping.
Arguments:
- `strategy` (string, REQUIRED): MUST be one of: 
  * 'TRIM_DEAD_LOGIC': Removes unused dangling gates/wires.
  * 'MERGE_EQUIVALENT': Uses SAT/FRAIG to merge functionally equivalent gates.
  * 'MINIMIZE_DEPTH': Restructures to minimize critical path.
  * 'CONSTRAIN_DEPTH': Restructures to meet a specific depth limit (Requires `target_value`). Can be applied to a specific logic cone if `target_net` is provided.
  * 'LIMIT_FANOUT': Inserts buffers to limit max loads (Requires `target_value`).
  * 'DEDICATED_BUFFERS': Inserts star-routing buffers for a specific net (Requires `target_net`).
  * 'PROPAGATE_CONSTANTS': Propagates and eliminates constants globally.
  * 'MAP_TO_GATE_SET': Globally reconstructs the entire netlist into a specific subset of gates while minimizing depth via ABC AIG balance. (Requires `target_net` to specify the allowed gates).
- `target_net` (string, OPTIONAL): The target net name. IF strategy is 'MAP_TO_GATE_SET', use this to pass the allowed gates (e.g., "AND, NOT" or "NAND").
- `target_value` (integer, OPTIONAL): The numeric constraint (e.g., 4 for fanout limit).

[TOOL inspect_netlist ARGUMENTS]
Description: General engine for inspecting netlist topology, properties, logic depth, and relationships. Use this tool to answer analytical questions about the design without modifying it.
Arguments:
- `query_type` (string, REQUIRED): MUST be exactly one of:
  * 'REPORT_GATE_TYPES': Reports total gate count and a breakdown of all gate types. Can be scoped to a specific fan-in cone if `target_a` is provided.
  * 'LIST_GATES_OF_TYPE': Lists all instances of a specific gate type with their input/output signals. (Requires `target_a` = gate type, e.g., "NAND"). Can be scoped to a fan-in cone if `target_b` = net name is provided.
  * 'MAX_DEPTH_TO_DFF': Reports maximum logic depth to a DFF.
  * 'VIOLATING_OUTPUTS': Counts outputs exceeding depth limit (Requires `threshold`).
  * 'IO_COUNT': Reports the number of primary I/Os, lists all primary input and output names, and automatically identifies all direct PI-to-PO connections (paths of length 0).
  * 'DEEPEST_OUTPUT': Find the specific output bit (or DFF D-pin) globally that has the deepest fan-in logic cone.
  * 'FIND_CONSTANT_GATES': Finds gates tied to constants.
  * 'LOGIC_DEPTH': Computes logic depth between two points (Requires `target_a`, `target_b`).
  * 'SHARED_FANIN': Reports shared fan-in logic (Requires `target_a`, `target_b`).
  * 'GATE_INFO': Retrieves I/O structure for a gate instance (Requires `target_a`).
  * 'NET_INFO': Retrieves driver/load info for a net (Requires `target_a`).
  * 'TRANSITIVE_FANOUT': Finds all reachable gates from a node (Requires `target_a`).
  * 'FANIN_CONE_SIZE' (or 'LOGIC_CONE_SIZE'): Counts gates in fan-in cone. Automatically traces through DFF Q-pins to D-pins (Requires `target_a` = net name).
  * 'ON_CRITICAL_PATH': Checks if a net/gate is on critical path (Requires `target_a`).
  * 'REG_TO_REG_PATHS': Analyzes register-to-register paths.
  * 'CHECK_SYMMETRY': Checks if the function at an output is symmetric with respect to two inputs. (Requires `target_a` = output_net, `target_b` = "input_net1, input_net2").
  * 'HIGHEST_FANOUT_PI': Identifies the primary input with the highest fanout.
  * 'LIST_DFFS_BY_CLOCK': Lists all flip-flops driven by a specific clock net (directly or via buffers). (Requires `target_a` = clock_net).
  * 'ARTICULATION_POINTS': Finds all nets that must be traversed on every path between two points. (Requires `target_a`, `target_b`).
  - `target_a` (string, OPTIONAL): Primary target name.
  - `target_b` (string, OPTIONAL): Secondary target name.
  - `threshold` (integer, OPTIONAL): Numeric constraint value for 'VIOLATING_OUTPUTS'.

  [TOOL reconnect_gate_pin ARGUMENTS]
  Description: Reconnect a specific input pin of a gate to a different internal signal.
  Arguments:
  - `gate_name` (string, REQUIRED): The instance name of the gate (e.g., 'g0').
  - `pin_name` (string, REQUIRED): The name of the pin (e.g., 'A', 'B', or for primitives, 'in0', 'in1', etc.).
  - `new_net_name` (string, REQUIRED): The name of the new net to connect to this pin.
[TOOL batch_logic_transformation ARGUMENTS]
Description: General logic transformation engine used to find and batch-replace specific gate types with functionally equivalent combinations within a defined scope.
Arguments:
- `scope` (string, REQUIRED): "global", "fanin_cone", or "fanout_cone".
- `target_gate_type` (string, REQUIRED): E.g., "OR", "AND", "XOR", "BACK_TO_BACK_NOT".
- `replacement_logic` (string, REQUIRED): Transformation rule (e.g., "NAND_NOT", "NOR_NOT", "AND_OR_NOT", "COLLAPSE").
- `target_net` (string, OPTIONAL): Central net for cone-based scoping. REQUIRED if scope is "fanin_cone" or "fanout_cone".

[CORE STRATEGY: HOW TO HANDLE COMPLEX TRANSFORMATIONS]
Example 1: "Find all back-to-back inverters and collapse them."
-> Explanation: Finding a specific pattern (back-to-back NOT) to replace.
-> Call: `batch_logic_transformation(scope="global", target_gate_type="BACK_TO_BACK_NOT", replacement_logic="COLLAPSE")`

Example 2: "Decompose all XOR gates in the fanin cone of output_Z into AND, OR, and NOT gates."
-> Explanation: Localized scope (fanin_cone) targeting a specific gate type (XOR).
-> Call: `batch_logic_transformation(scope="fanin_cone", target_net="output_Z", target_gate_type="XOR", replacement_logic="AND_OR_NOT")`

Example 3: "Reconstruct the ENTIRE design to use strictly ONLY AND and NOT gates while preserving equivalence."
-> Explanation: Algorithmic mathematical mapping of the whole design, not targeting one specific gate type to replace.
-> Call: `netlist_optimization_engine(strategy="MAP_TO_GATE_SET", target_net="AND, NOT")`

Example 4: "For each output with depth greater than 4, optimize its cone to meet the depth constraint."
-> Explanation: Deep algorithmic optimization based on depth globally.
-> Call: `netlist_optimization_engine(strategy="CONSTRAIN_DEPTH", target_value=4)`

Example 4.1: "Reduce the depth of the cone of output_Z to 4."
-> Explanation: Algorithmic optimization of depth for a specific logic cone.
-> Call: `netlist_optimization_engine(strategy="CONSTRAIN_DEPTH", target_net="output_Z", target_value=4)`

Example 5: "Remove any dangling gates and nets that do not affect any primary output."
-> Explanation: Deep algorithmic optimization for dead logic.
-> Call: `netlist_optimization_engine(strategy="TRIM_DEAD_LOGIC")`

Example 6: "Find all paths of length 0 (direct wire connections from PI to PO)."
-> Explanation: Finding direct wire connections of length 0 from PI to PO.
-> Call: `inspect_netlist(query_type="IO_COUNT")`

[ADVANCED PROBLEM SOLVING LOGIC]
- If asked: "Does EVERY path from A to B pass through gate C?" 
  -> Do NOT guess. Use `analyze_paths(start_net=A, end_net=B, avoid_net=C, find_all=False)`. 
  -> You only need ONE counter-example. NEVER use find_all=True for this check.
  -> If the tool returns "No paths found", the answer is YES (gate C blocks all paths).
  -> If the tool returns a path, the answer is NO.

- If asked to "Report", "Find", or "List" gates with constant inputs:
  -> Call: `inspect_netlist(query_type="FIND_CONSTANT_GATES", target_a="<gate_type>")`
  -> Do NOT optimize them yet! Just report what is found.

- If asked to "Simplify", "Propagate", or "Eliminate" using constants:
  -> Call: `netlist_optimization_engine(strategy="PROPAGATE_CONSTANTS")`

[SIGNAL SEARCH & EQUIVALENCE]
- If asked: "Is there a signal equivalent to X?" or "Find a signal equivalent to NOT(X)".
  -> Call: `find_equivalent_signal(target_net=X, inverted=False)` (or True for NOT(X)).
- If asked: "Does there exist any pair (a, b) such that NAND(a, b) is equivalent to n25?"
  -> Call: `find_equivalent_combination(target_net="n25", gate_type="NAND")`
  -> Supported gate types: AND, OR, NAND, NOR, XOR, XNOR.


[ANSWERING HISTORY QUESTIONS & NUMERICAL PRECISION]
When asked follow-up questions about previous actions:
1. You MUST use a Chain of Thought approach. Write a brief [Thought] block explaining the tool output.
2. CRITICAL RULE FOR ZERO: If the tool reported "0" or "No gates were...", you MUST explicitly include the digit "0".
3. NEVER start a sentence with the digit "0". If the answer is 0, you must start the sentence with "There were..." or "A total of...". 
   - Good: "There were 0 dangling gates removed."
   - Good: "A total of 0 NAND gates were eliminated."
   - BAD: "0 dangling gates were removed." (Will cause parsing failure)

[PROBLEM SOLVING WORKFLOW]
If a request is ambiguous or requires analysis before modification:
1. First, call analysis tools to understand the current state.
2. Read the tool output.
3. Call the appropriate transformation tools.
4. Call `prove_equivalence` to ensure you didn't break the circuit.
5. Provide your final `#RESPONSE`.

[CRITICAL RULE: NUMERICAL PRECISION & LOG GROUNDING]
When summarizing the execution results of the tools to the user, you MUST strictly and literally cite the EXACT numbers and metrics returned by the python tool execution logs.
1. DO NOT generalize, smooth out, or use vague templates.
2. If a count or value is 0, you must state explicitly that it is 0 or that no such components were affected.
3. Any summarization that distorts, smooths over, or hallucinates numerical facts from the tool outputs will be considered a catastrophic verification failure.

[EXAMPLE RESPONSE FORMAT]
- Bad: "Dangling gates and floating wires have been successfully trimmed from the design."
- Good: "No unused gates were found (0 dead gates removed). 14 floating wires were successfully removed."

CRITICAL RULES FOR OUTPUT FORMAT:
For the immediate request line provided by the user, you MUST provide your answer wrapped inside a clean #RESPONSE block. Do NOT include manual question numbers or IDs.

Example Final Output Structure:
#RESPONSE
(Provide the exact and precise answer for the current step here...)
#END
"""

# config = types.GenerateContentConfig(
#     system_instruction=SYSTEM_PROMPT,
#     temperature=0.2,
#     # 記得把所有工具都放進這個列表
#     tools=[], 
# )

# chat = client.chats.create(
#     model="gemini-2.5-flash", 
#     config=config
# )
# ==========================================
# 4. 主對話引擎
# ==========================================
def ask_agent(user_message):
    global chat
    
    log_output(f"\n[User Request from Prompt]:\n{user_message}")
    log_output("\n... Gemini is thinking and acting ...")

    max_retries = 3      
    base_delay = 5       
    final_text = ""

    for attempt in range(max_retries + 1):
        try:
            # 🚀 直接傳送使用者的原始訊息給模型
            response = chat.send_message(user_message)
            
            # 優先檢查是否漏出了未執行的 function_calls (代表 SDK 被迫中斷)
            if response.function_calls:
                log_output("\n[System Warning]: 觸發 SDK 上限！Agent 未能執行完所有工具。")
                if response.text:
                    final_text = response.text.strip()
                    log_output("\n[被中斷的作答]:\n" + final_text)
            
            # 正常執行完畢，取得文字
            elif response.text:
                final_text = response.text.strip()
                
                # ✨ System Bypass: 攔截兌換券並還原 (處理百萬條路徑等巨量輸出)
                global system_bypass_cache
                for key, massive_content in system_bypass_cache.items():
                    if key in final_text:
                        log_output(f"\n[System 代筆介入]: 偵測到兌換券 {key}，正在將巨量路徑注入輸出...")
                        final_text = final_text.replace(key, massive_content)
                
                # 💡 注意：這裡不再呼叫 log_output(final_text) 和 write_final_log()
                # 因為這兩件事情已經交給最底下的 __main__ 迴圈去統一處理了，避免重複印出！
                
            # ✨ 終極防護：當 LLM 傳回完全空白的封包時，電擊喚醒它！
            else:
                log_output("\n[System Warning]: 偵測到 Gemini 伺服器回傳空白！正在發送電擊訊號...")
                
                wake_up_prompt = (
                    "CRITICAL ERROR: You returned an empty response. "
                    f"Here is the ORIGINAL task you MUST execute immediately:\n\n{user_message}\n\n"
                    "Start executing the necessary tools NOW, and provide your answer!"
                )
                
                retry_resp = chat.send_message(wake_up_prompt)
                
                if retry_resp.text:
                    final_text = retry_resp.text.strip()
                    
                    # 同樣執行 Bypass 還原邏輯
                    for key, massive_content in system_bypass_cache.items():
                        if key in final_text:
                            final_text = final_text.replace(key, massive_content)
                else:
                    final_text = "[System Error]: Gemini 徹底放棄思考。"
                    log_output("\n" + final_text)

            # 🚀 成功處理完畢，回傳給主迴圈
            return final_text  
            
        except Exception as e:
            err_msg = str(e)
            # 攔截 503 (伺服器忙碌) 與 429 (請求過於頻繁) 並執行指數退避重試
            if "503" in err_msg or "429" in err_msg:
                if attempt < max_retries:
                    wait_time = base_delay * (2 ** attempt) 
                    log_output(f"\n[Warning]: 伺服器忙碌中 (503/429)。等待 {wait_time} 秒後重試...")
                    time.sleep(wait_time)
                else:
                    msg = f"[System Error]: 已達到最大重試次數，放棄本次請求。錯誤: {err_msg}"
                    log_output("\n" + msg)
                    return msg
            # 攔截其他未知的系統崩潰錯誤
            else:
                msg = f"[System Error]: 發生未知的嚴重錯誤: {err_msg}"
                log_output("\n" + msg)
                return msg

    return final_text

# ==========================================
# 5. 雙模式執行 (CLI 參數解析與 Interactive I/O)
# ==========================================
# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="ICCAD 2026 Problem A - EDA Agent")
#     parser.add_argument("-config", type=str, default="config.yaml", help="設定檔的路徑")
#     parser.add_argument("prompt_file", type=str, nargs="?", default=None, help="要執行的 prompt.txt 路徑 (選填)")
    
#     args, unknown = parser.parse_known_args()

#     # --- 讀取設定檔 (YAML) ---
#     config_path = os.path.abspath(args.config)
#     if not os.path.exists(config_path):
#         print(f"[Fatal Error]: 找不到設定檔 '{config_path}'！", file=sys.stderr)
#         sys.exit(1)
        
#     with open(config_path, 'r', encoding='utf-8') as f:
#         config_data = yaml.safe_load(f)

#     gemini_cfg = config_data.get("gemini", {})
#     gen_cfg = config_data.get("generation", {})
    
#     api_key = gemini_cfg.get("api_key")
#     model_name = gemini_cfg.get("model", "gemini-2.5-flash")
#     temperature = gen_cfg.get("temperature", 0.2)

#     if not api_key:
#         print("[Fatal Error]: 設定檔中沒有找到 Gemini API Key！", file=sys.stderr)
#         sys.exit(1)

#     client = genai.Client(api_key=api_key)
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ICCAD 2026 Problem A - EDA Agent")
    parser.add_argument("-config", type=str, default="config.yaml", help="設定檔的路徑")
    parser.add_argument("prompt_file", type=str, nargs="?", default=None, help="要執行的 prompt.txt 路徑 (選填)")
    
    args, unknown = parser.parse_known_args()

    # --- 讀取設定檔 (YAML) ---
    config_path = os.path.abspath(args.config)
    if not os.path.exists(config_path):
        print(f"[Fatal Error]: 找不到設定檔 '{config_path}'！", file=sys.stderr)
        sys.exit(1)
        
    with open(config_path, 'r', encoding='utf-8') as f:
        config_data = yaml.safe_load(f)

    gemini_cfg = config_data.get("gemini", {})
    gen_cfg = config_data.get("generation", {})
    
    # ---------------------------------------------------------
    # 【修改重點 1】: 放棄 api_key，改為讀取 project_id 與 location
    # ---------------------------------------------------------
    project_id = gemini_cfg.get("project_id")
    location = gemini_cfg.get("location", "us-central1") # Vertex AI 需要指定區域
    model_name = gemini_cfg.get("model", "gemini-2.5-flash")
    temperature = gen_cfg.get("temperature", 0.2)

    if not project_id:
        print("[Fatal Error]: 設定檔中沒有找到 GCP Project ID！", file=sys.stderr)
        sys.exit(1)

    # ---------------------------------------------------------
    # 【修改重點 2】: 初始化 Vertex AI 模式的 Client
    # ---------------------------------------------------------
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location=location
    )    
    
    gemini_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=temperature,
        tools=[read_design, write_design, analyze_paths, 
               check_equivalence, find_equivalent_signal, find_equivalent_combination,
               check_clock_domain, check_property,
               rename_element,
               derive_boolean_equation, 
               verify_internal_signals,
               prove_equivalence, report_last_added_buffer_count,
               batch_logic_transformation, netlist_optimization_engine,
               inspect_netlist,
               reconnect_gate_pin, check_symmetry, report_highest_fanout_pi]  # 記得把所有工具都放進這個列表, 
    )
    
    chat = client.chats.create(model=model_name, config=gemini_config)

    # ==========================================
    # 💡 核心改寫：自適應資料源分流 (File Stream vs Stdin Stream)
    # ==========================================
    lines_source = []
    is_interactive = True

    if args.prompt_file and os.path.exists(args.prompt_file):
        # 【模式 A】: 檔案讀檔模式
        prompt_dir = os.path.dirname(os.path.abspath(args.prompt_file))
        test_name = os.path.basename(prompt_dir)
        LOG_FILE_PATH = os.path.join(prompt_dir, f"{test_name}.log")

        print(f"\n[System]: 進入【檔案讀檔模式】 (File Mode)")
        print(f"[System]: 準備【逐行】依序執行測資: {args.prompt_file}\n")
        
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            lines_source = f.readlines()
        is_interactive = False
    else:
        # 【模式 B】: AutoGrader 評測互動模式
        prompt_dir = os.getcwd()
        LOG_FILE_PATH = os.path.join(prompt_dir, "agent_output.log") 
        
        print(f"\n[System]: 進入【逐行互動模式】 (Interactive AutoGrader Mode)")
        print(f"[System]: 等待標準輸入 (stdin) 中...\n")
        
        lines_source = sys.stdin
        is_interactive = True
        
    # 🚀 啟動前初始化：清空或建立乾淨的 Log 檔案
    with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
        f.write("")

    # ==========================================
    # 🎯 統一的逐行排程調度引擎 (完全同構運作)
    # ==========================================
    request_id = 1
    
    for line in lines_source:
        prompt = line.strip()
        if not prompt:
            continue  # 跳過空行
            
        # 如果是檔案模式，在終端機印出當前正在執行的 Prompt，方便 Debug 對齊
        if not is_interactive:
            print(f"\n[Prompt Line {request_id}]: {prompt}")
            
        try:
            # 1. 送出單行指令，獲取 Agent 回傳值
            raw_response = ask_agent(prompt)
            
            # 2. 強制清洗 LLM 自帶的干擾標籤，確保題號完全受控於 Python
            clean_text = raw_response
            clean_text = re.sub(r'#RESPONSE\s*\d*', '', clean_text)
            clean_text = re.sub(r'#END\s*\d*', '', clean_text)
            clean_text = clean_text.strip()
            
            # 3. 重新包裝成符合大會規格、絕不跳號的嚴格格式
            final_formatted_response = f"#RESPONSE {request_id}\n{clean_text}"
            
            # 4. 印出至標準輸出 (stdout)
            print(final_formatted_response)
            
            # 5. 將本次互動歷史追加 (append) 寫入對應的 .log 檔案中
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(final_formatted_response + f"\n#END {request_id}\n\n")
                
        except Exception as e:
            # 強大防死鎖：萬一中途出錯，也要保證印出 #END，絕不讓 AutoGrader 卡死
            err_msg = f"System Error during execution: {str(e)}"
            print(err_msg)
            
        # 6. 精準印出大會要求的單步結束標記
        print(f"#END {request_id}")
        
        # 7. 強制推播緩衝區，完成一問一答的交握 (Handshake)
        sys.stdout.flush()
        
        request_id += 1

    # 檔案模式結束後的小彩蛋提示
    if not is_interactive:
        print(f"\n======================================")
        print(f"[System 檔案模式執行完畢 🎉]")
        print(f"  👉 完美的 1 ~ {request_id-1} 完整歷程已存至: {LOG_FILE_PATH}")
        print(f"======================================")

    # ==========================================
    # 🌟 神級收尾：自動清理 original_*.v 暫存檔
    # ==========================================
    try:
        if prompt_dir and os.path.exists(prompt_dir):
            import glob
            temp_files = glob.glob(os.path.join(prompt_dir, "original_*.v"))
            if temp_files:
                print(f"\n[System]: 偵測到 {len(temp_files)} 個 original_*.v 暫存檔，正在自動清理...")
                for f in temp_files:
                    try:
                        os.remove(f)
                    except:
                        pass
                print("[System]: 清理完畢，環境已恢復整潔。")
    except Exception as e:
        print(f"[System Warning]: 自動清理暫存檔時發生錯誤: {str(e)}")
    # 模擬 連續問答
    # ask_agent("Hello! Please load the file 'test.v'.")
    # ask_agent("Does every path from 'in_a' to 'y_d' pass through 'n19'?")
    # ask_agent("What is the maximum logic depth of the current design? Please also show me the path.")
    # ask_agent("Can you find a path from 'in_a' to 'n4' that avoids 'n3'?")
    # ask_agent("Report all primary outputs whose logic cone contains more than 3 gates.")

    # 考驗 1：明顯不等價的兩條線
    # ask_agent("Are net 'n9' and net 'n14' logically equivalent? If not, why?")
    
    # 考驗 2：尋找等價的線索 (LLM 會自己思考要呼叫誰來驗證！)
    # ask_agent("Is there any signal in the design that is logically equivalent to NOT(n1)?")
    # ask_agent("Do DFF1 and DFF2 operate under the same clock domain?")
    # ask_agent("For output 'out_z', verify that it is asserted only when 'n6' is 1 and 'rst_n' is 1. Please show me the counterexample variables if it fails.")
